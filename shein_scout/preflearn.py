# -*- coding: utf-8 -*-
"""
shein_scout.preflearn —— 从用户标注里学"个人审美"

循环：
  1. patterncrawl 爬一批图（100+ 张）
  2. labeler 生成打标签页面，用户标 好/坏/跳过
  3. 本模块学习：哪些视觉特征与"好"相关
  4. 下一批爬取/筛选时按学到的规律排序 —— 逐步逼近个人审美

设计取舍：
  * 用**可解释特征**而不是黑盒：要能告诉用户"你喜欢什么"，才能验证学对了
  * 留一批验证（leave-one-batch-out）：检验学到的是不是能泛化到新图，
    而不是记住了这一批
  * 样本少时明确说"还不能用"，不糊弄
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import features

STORE = "labels.jsonl"        # 每批的标注存这里（追加）
FEAT_CACHE = "feat_cache.json"


# ------------------------------------------------------------------ 标注存取
def store_path(root: Path) -> Path:
    return root / "work" / "preference"


def append_labels(root: Path, rows: list[dict]) -> int:
    """把标注写回图案库。返回新增数。"""
    from . import library
    cur = {r.key(): r for r in library.load(root)}
    n_new = 0
    for x in rows:
        k = f"{x.get('batch')}/{x.get('rel') or x.get('file')}"
        if k not in cur:
            n_new += 1
        library.merge(root, [library.Rec(
            batch=x.get("batch", ""), rel=x.get("rel", ""),
            file=x.get("file", ""), keyword=x.get("keyword", ""),
            source=x.get("source", ""), label=x.get("label", ""),
            note=x.get("note", ""), used=bool(x.get("used")),
        )])
    return n_new


def _legacy_append_labels(root: Path, rows: list[dict]) -> int:
    """（旧实现，保留以防外部调用）"""
    d = store_path(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / STORE
    existing = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                existing[(r.get("batch"), r.get("file"))] = r
            except Exception:
                pass
    n_new = 0
    for r in rows:
        k = (r.get("batch"), r.get("file"))
        if k not in existing:
            n_new += 1
        existing[k] = r
    with open(p, "w", encoding="utf-8") as fh:
        for r in existing.values():
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return n_new


def load_labels(root: Path) -> list[dict]:
    """从图案库读已标注的记录（统一数据源，不再单独维护标签文件）。"""
    from . import library
    return [{"batch": r.batch, "file": r.file, "rel": r.rel,
             "label": r.label, "keyword": r.keyword, "source": r.source,
             "pred": r.pred}
            for r in library.load(root) if r.label in ("good", "bad")]


def save_predictions(root: Path, pairs: list[tuple[str, float]]) -> int:
    """把模型预测分写回图案库（pairs: [(key, score), ...]）。"""
    from . import library
    recs = library.load(root)
    m = dict(pairs)
    n = 0
    for r in recs:
        if r.key() in m:
            r.pred = float(m[r.key()])
            n += 1
    if n:
        library.save(root, recs)
    return n


def find_image(root: Path, batch: str, fname: str, rel: str = "") -> Path | None:
    """在批次目录里找图片文件。

    优先用 rel（子目录/文件名）—— 因为不同子目录里文件名会重名（都从 0001 起），
    只用文件名会定位到错图。
    """
    base = root / "patterns" / batch
    if not base.exists():
        return None
    from . import library as _lib
    p = _lib.img_path(root, batch, rel, fname)
    return p if p.exists() else None


# ------------------------------------------------------------------ 特征缓存
def _feat_cache_path(root: Path) -> Path:
    return store_path(root) / FEAT_CACHE


def build_feature_table(root: Path, labels: list[dict], verbose: bool = True
                        ) -> tuple[list[dict], np.ndarray, list[str]]:
    """把标注转成特征矩阵。返回 (有效标注, X 矩阵, 特征名列表)"""
    d = store_path(root)
    d.mkdir(parents=True, exist_ok=True)
    cp = _feat_cache_path(root)
    cache = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}

    rows, feats = [], []
    n_missing = 0
    for r in labels:
        if r.get("label") not in ("good", "bad"):
            continue
        key = f"{r.get('batch')}/{r.get('rel') or r.get('file')}"
        f = cache.get(key)
        if f is None:
            p = find_image(root, r.get("batch", ""), r.get("file", ""),
                           r.get("rel", ""))
            if not p:
                n_missing += 1
                continue
            f = features.extract(p)
            if not f or not f.get("ok"):
                n_missing += 1
                continue
            cache[key] = f
        rows.append(r)
        feats.append(f)
    cp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")

    if verbose:
        print(f"  有效标注 {len(rows)} 条（缺图/失败 {n_missing}）")

    if not rows:
        return [], np.zeros((0, 0)), []

    # 选数值特征（排除非数值与常量）
    names = []
    for k, v in feats[0].items():
        if isinstance(v, bool) or isinstance(v, (int, float)):
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                continue
            names.append(k)
    names = [n for n in names if n not in ("ok",)]
    # 去掉常量列
    keep = []
    for n in names:
        col = [float(f.get(n) or 0) for f in feats]
        if max(col) - min(col) > 1e-9:
            keep.append(n)
    names = keep

    X = np.array([[float(f.get(n) or 0) for n in names] for f in feats], dtype=float)
    return rows, X, names


# ------------------------------------------------------------------ 简单逻辑回归
@dataclass
class PrefModel:
    names: list[str] = field(default_factory=list)
    w: list[float] = field(default_factory=list)
    b: float = 0.0
    mu: list[float] = field(default_factory=list)
    sd: list[float] = field(default_factory=list)

    def proba(self, xs: np.ndarray) -> np.ndarray:
        w = np.array(self.w)
        mu = np.array(self.mu)
        sd = np.array(self.sd)
        z = ((xs - mu) / sd) @ w + self.b
        return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))

    def to_json(self) -> dict:
        return {"names": self.names, "w": self.w, "b": self.b,
                "mu": self.mu, "sd": self.sd}


def fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 1.0, lr: float = 0.1,
              iters: int = 800) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """梯度下降 + L2。返回 (w, b, mu, sd)"""
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd < 1e-9] = 1.0
    Xs = (X - mu) / sd
    n, d = Xs.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(iters):
        z = Xs @ w + b
        p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
        g = p - y
        gw = Xs.T @ g / n + l2 * w / n
        gb = float(g.mean())
        w -= lr * gw
        b -= lr * gb
    return w, b, mu, sd


def metrics(p: np.ndarray, y: np.ndarray) -> dict:
    pred = (p >= 0.5).astype(float)
    acc = float((pred == y).mean())
    tp = float(((pred == 1) & (y == 1)).sum())
    fp = float(((pred == 1) & (y == 0)).sum())
    fn = float(((pred == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"acc": acc, "precision": prec, "recall": rec, "f1": f1,
            "n": int(len(y)), "pos": int(y.sum())}


def learn(root: Path, verbose: bool = True) -> dict:
    """学一轮。返回结果摘要。"""
    labels = load_labels(root)
    if verbose:
        print(f"  总标注 {len(labels)} 条")
    rows, X, names = build_feature_table(root, labels, verbose=verbose)
    if len(rows) < 12:
        return {"ok": False,
                "msg": f"有效样本只有 {len(rows)} 条，至少需要 12 条（建议 30+）才能学。"}

    y = np.array([1.0 if r["label"] == "good" else 0.0 for r in rows])
    if y.sum() < 4 or (len(y) - y.sum()) < 4:
        return {"ok": False, "msg": f"好/坏样本太不均衡（好 {int(y.sum())}、"
                                    f"坏 {int(len(y)-y.sum())}），各需至少 4 条。"}

    # 交叉验证（留一批）
    batches = sorted({r.get("batch", "") for r in rows})
    cv = []
    if len(batches) >= 2:
        for b in batches:
            te = np.array([r.get("batch") == b for r in rows])
            if te.sum() == 0 or te.sum() == len(rows):
                continue
            m = ~te
            if y[m].sum() < 2 or (len(y[m]) - y[m].sum()) < 2:
                continue
            w, bb, mu, sd = fit_logit(X[m], y[m])
            mdl = PrefModel(names=names, w=w.tolist(), b=bb,
                            mu=mu.tolist(), sd=sd.tolist())
            cv.append(metrics(mdl.proba(X[te]), y[te]))

    w, b, mu, sd = fit_logit(X, y)
    mdl = PrefModel(names=names, w=w.tolist(), b=b, mu=mu.tolist(), sd=sd.tolist())
    insample = metrics(mdl.proba(X), y)

    # 特征重要性（标准化后权重绝对值）
    order = np.argsort(-np.abs(w))
    top = [(names[i], float(w[i])) for i in order[:18]]

    d = store_path(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "pref_model.json").write_text(json.dumps({
        "model": mdl.to_json(), "top": top, "cv": cv,
        "insample": insample, "n": len(rows),
        "batches": batches,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if verbose:
        print(f"\n  样本内: 准确率 {insample['acc']:.1%}  F1 {insample['f1']:.3f}  "
              f"（好 {insample['pos']} / 坏 {len(y)-insample['pos']}）")
        if cv:
            m = np.mean([c["acc"] for c in cv])
            print(f"  留一批验证: 平均准确率 {m:.1%}（{len(cv)} 折）"
                  f"  —— 这才是对新图有效的数字")
        else:
            print("  留一批验证: 批次不足 2 个，无法验证泛化能力")
        print("\n  你偏好的前 12 个特征（正=喜欢，负=不喜欢）:")
        for n, v in top[:12]:
            bar = ("+" if v > 0 else "-") * min(18, int(abs(v) * 6) + 1)
            print(f"    {n:24s} {v:+.3f}  {bar}")

    return {"ok": True, "model": mdl, "top": top, "cv": cv,
            "insample": insample, "n": len(rows), "batches": batches, "names": names}


def rank_batch(root: Path, recs: list, model: "PrefModel | None" = None
               ) -> list[tuple[str, float]]:
    """给一批图预测"合你审美"的分数。没有模型就返回空。"""
    if model is None:
        return []
    import numpy as _np
    from . import features as _ft
    pairs = []
    for r in recs:
        p = library.img_path(root, r.batch, r.rel, r.file)
        if not p.exists():
            continue
        f = _ft.extract(p)
        if not f or not f.get("ok"):
            continue
        x = _np.array([[float(f.get(n) or 0) for n in model.names]])
        pairs.append((r.key(), float(model.proba(x)[0])))
    return pairs


def load_model(root: Path):
    """读已学的偏好模型，没有返回 None。"""
    p = store_path(root) / "pref_model.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))["model"]
        return PrefModel(names=d["names"], w=d["w"], b=d["b"],
                         mu=d["mu"], sd=d["sd"])
    except Exception:
        return None
