# -*- coding: utf-8 -*-
"""
shein_scout.library —— 图案库

一个工具，一条流水线：
    爬图 → （可选）按你已标的偏好排序 → 打标签 → 好的进库

图案库职责：
  * 统一存所有图（含未标的），一条记录一张图
  * 记录标签（好/坏/跳过）、备注、是否已用
  * 按标签/关键词/备注搜索，导出
  * 给学习排序提供训练数据

存储：patterns/library.jsonl（一行一条，追加式，好合并、不怕中断）
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

LIB = ("patterns", "library.jsonl")


@dataclass
class Rec:
    """图案库里的一条记录。"""
    batch: str = ""
    rel: str = ""              # 相对批次目录的路径（唯一标识）
    file: str = ""
    keyword: str = ""
    source: str = ""
    title: str = ""
    page_url: str = ""
    img_url: str = ""
    width: int = 0
    height: int = 0
    bytes: int = 0              # 文件大小（用于判重辅助）
    sha1: str = ""              # 内容哈希（跨批次判重的唯一依据）
    tags: dict = field(default_factory=dict)   # AI 打的标签 {维度:[标签]}
    label: str = ""            # good / bad / skip / ""(未标)
    note: str = ""             # 用户备注
    used: bool = False         # 是否已用在商品上
    used_at: str = ""          # 标记已用的时间
    pred: float = -1.0         # 打标签时的模型预测分（用于看模型准不准）
    added: str = ""

    def key(self) -> str:
        return f"{self.batch}/{self.rel or self.file}"


def img_path(root: Path, batch: str, rel: str, fname: str = "") -> Path:
    """把 (batch, rel) 还原成图片真实路径。

    容错：早期版本的 rel 里多带了批次名一层（"批次/子目录/文件名"），
    这里自动剥掉，免得已爬的数据作废。
    """
    base = root / "patterns" / batch
    r = (rel or fname or "").replace("\\", "/").lstrip("/")
    if not r:
        return base
    parts = r.split("/")
    # 剥掉重复的批次名
    while parts and parts[0] == batch:
        parts = parts[1:]
    if len(parts) > 1:
        return base.joinpath(*parts)
    # 只有文件名：在子目录里找
    for sub in sorted(base.iterdir()) if base.exists() else []:
        if sub.is_dir() and (sub / parts[0]).exists():
            return sub / parts[0]
    return base / parts[0]


def lib_path(root: Path) -> Path:
    return root.joinpath(*LIB)


def load(root: Path) -> list[Rec]:
    p = lib_path(root)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        out.append(Rec(**{k: v for k, v in d.items()
                          if k in Rec.__dataclass_fields__}))
    return out


def save(root: Path, recs: list[Rec]) -> Path:
    p = lib_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    return p


def merge(root: Path, new_recs: list[Rec]) -> tuple[int, int]:
    """把新记录并入库。按 key 去重，已存在则更新标签/备注/已用状态。
    返回 (新增数, 更新数)"""
    cur = {r.key(): r for r in load(root)}
    n_new = n_upd = 0
    for nr in new_recs:
        k = nr.key()
        if k not in cur:
            if not nr.added:
                nr.added = time.strftime("%Y-%m-%d %H:%M")
            cur[k] = nr
            n_new += 1
        else:
            old = cur[k]
            changed = False
            for f in ("label", "note", "used", "used_at", "pred", "tags"):
                v = getattr(nr, f)
                if v not in ("", False, -1.0, {}) and getattr(old, f) != v:
                    setattr(old, f, v)
                    changed = True
            if changed:
                n_upd += 1
            # 补齐缺失的元信息
            for f in ("keyword", "source", "title", "page_url", "img_url",
                      "width", "height"):
                if not getattr(old, f) and getattr(nr, f):
                    setattr(old, f, getattr(nr, f))
    save(root, list(cur.values()))
    return n_new, n_upd


def add_from_manifest(root: Path, batch: str) -> int:
    """把一个批次的 manifest 全部导入库（未标状态）。"""
    from . import patterncrawl as pc
    imgs = pc.load_manifest(root / "patterns", batch)
    recs = [Rec(batch=x.batch, rel=x.rel, file=x.file, keyword=x.keyword,
                source=x.source, title=x.title, page_url=x.page_url,
                img_url=x.img_url, width=x.width, height=x.height,
                bytes=getattr(x, "bytes", 0), sha1=getattr(x, "sha1", ""))
            for x in imgs]
    n, u = merge(root, recs)
    return n


def search(root: Path, label: str = "", keyword: str = "", text: str = "",
           used: str = "", batch: str = "") -> list[Rec]:
    """搜索图案库。label: good/bad/skip/un; used: yes/no/空"""
    out = []
    for r in load(root):
        if label == "un" and r.label:
            continue
        if label and label != "un" and r.label != label:
            continue
        if keyword and keyword.lower() not in (r.keyword or "").lower():
            continue
        if text:
            t = text.lower()
            if t not in (r.title or "").lower() and t not in (r.note or "").lower():
                continue
        if used == "yes" and not r.used:
            continue
        if used == "no" and r.used:
            continue
        if batch and r.batch != batch:
            continue
        out.append(r)
    return out


def mark_used(root: Path, keys: list[str], used: bool = True) -> int:
    """把指定记录标为已用/未用。keys 可以是 rel / file / key。"""
    recs = load(root)
    ks = set(keys)
    n = 0
    for r in recs:
        if r.key() in ks or r.rel in ks or r.file in ks:
            r.used = used
            r.used_at = time.strftime("%Y-%m-%d %H:%M") if used else ""
            n += 1
    if n:
        save(root, recs)
    return n


def annotate(root: Path, keys: list[str], note: str) -> int:
    """给记录加备注。"""
    recs = load(root)
    ks = set(keys)
    n = 0
    for r in recs:
        if r.key() in ks or r.rel in ks or r.file in ks:
            r.note = note
            n += 1
    if n:
        save(root, recs)
    return n


def stats(root: Path) -> dict:
    recs = load(root)
    return {
        "total": len(recs),
        "good": sum(1 for r in recs if r.label == "good"),
        "bad": sum(1 for r in recs if r.label == "bad"),
        "skip": sum(1 for r in recs if r.label == "skip"),
        "unlabeled": sum(1 for r in recs if not r.label),
        "used": sum(1 for r in recs if r.used),
        "batches": len({r.batch for r in recs}),
        "keywords": len({r.keyword for r in recs if r.keyword}),
    }


def export_csv(root: Path, out: Path, recs: list[Rec] | None = None) -> Path:
    recs = recs if recs is not None else load(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["批次", "文件", "标签", "已用", "备注", "关键词", "来源",
                    "尺寸", "标题", "图片链接", "来源页"])
        for r in recs:
            w.writerow([r.batch, r.rel or r.file, r.label, "是" if r.used else "",
                        r.note, r.keyword, r.source,
                        f"{r.width}x{r.height}" if r.width else "", r.title,
                        r.img_url, r.page_url])
    return out


def export_good(root: Path, out_dir: Path, copy_files: bool = False) -> dict:
    """导出所有"好"的图案。返回统计。"""
    import shutil
    recs = [r for r in load(root) if r.label == "good"]
    out_dir.mkdir(parents=True, exist_ok=True)
    n_copy = 0
    if copy_files:
        for r in recs:
            src = img_path(root, r.batch, r.rel, r.file)
            if src.exists():
                dst = out_dir / f"{r.keyword[:20]}_{r.rel.replace('/', '_')}"
                try:
                    shutil.copy2(src, dst)
                    n_copy += 1
                except Exception:
                    pass
    idx = export_csv(root, out_dir / "好的图案.csv", recs)
    return {"n": len(recs), "copied": n_copy, "csv": idx}


# ---------------------------------------------------------------- 标签偏好
def all_tags(root: Path) -> dict[str, dict[str, int]]:
    """统计图案库里出现过的所有标签及图片数。返回 {维度: {标签: 数量}}"""
    from collections import Counter
    out: dict[str, Counter] = {}
    for r in load(root):
        for dim, vals in (r.tags or {}).items():
            out.setdefault(dim, Counter())
            for v in vals:
                out[dim][v] += 1
    return {d: dict(c.most_common()) for d, c in out.items()}


def pref_path(root: Path) -> Path:
    return root / "work" / "preference" / "tag_prefs.json"


def load_prefs(root: Path) -> dict:
    """读标签偏好：{维度: {标签: 权重}}，权重 = 用户点亮的强度。"""
    p = pref_path(root)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_prefs(root: Path, prefs: dict) -> Path:
    p = pref_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def set_prefs(root: Path, picked: dict) -> dict:
    """设置偏好。picked: {维度: [标签,...]} 或 {维度: {标签: 权重}}"""
    prefs = {}
    for dim, v in (picked or {}).items():
        if isinstance(v, dict):
            prefs[dim] = {k: float(w) for k, w in v.items() if w}
        else:
            prefs[dim] = {k: 1.0 for k in v}
    save_prefs(root, prefs)
    return prefs


def score_by_prefs(root: Path, recs: list) -> dict[str, float]:
    """按标签偏好给图打分（命中越多、权重越高，分越高）。返回 {key: 分数}"""
    from . import tagger
    prefs = load_prefs(root)
    if not prefs:
        return {}
    out = {}
    for r in recs:
        s = 0.0
        hit = 0
        for dim, vals in (r.tags or {}).items():
            dw = tagger.DIM_WEIGHT.get(dim, 1.0)
            want = prefs.get(dim) or {}
            for v in vals:
                if v in want:
                    s += dw * float(want[v])
                    hit += 1
        out[r.key()] = (s, hit)   # 返回元组，排序时用
    return out
