# -*- coding: utf-8 -*-
"""
shein_scout.learn —— 口径校准 + 偏好模型

思路（重要）
------------
市面上所谓"AI 猜买手喜好"基本都是黑盒。这个工具走的是可复查的路线：

1. **口径校准（profile）**：拿你**已过审/已上架**的图作为正样本，统计每张图
   的特征中位数与离散度，得到一份"目标风格画像"。新图与画像的偏离程度就是
   style_match —— 它衡量的是"这张图有多像已经被平台接受的那批图"。

2. **偏好模型（model）**：拿"过审 vs 被刷"两组图做逻辑回归（纯 numpy 实现，
   带 L2 正则 + 类别平衡 + 特征标准化）。它学的是**平台刷图决策的判别边界**，
   而不是泛泛的美学。样本少时自动降权，避免过度自信。

3. **合规硬规则（rules）**：尺寸/比例/水印/白底等待判定项，纯规则，命中即扣，
   这部分永远是"确定性"的。

最终 preference 分数 = 三者加权。任何一项数据不足都会自动降级并明确告诉你，
不会假装有把握。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------
# 用于"风格画像"的特征（连续量，越稳越好；不放 phash/blobs 这类离散计数）
# --------------------------------------------------------------------------
STYLE_FEATURES = [
    "bright_mean", "bright_p50", "bright_std", "contrast_rms", "contrast_range",
    "sat_mean", "sat_p90", "L_mean", "colorfulness", "color_entropy",
    "white_ratio", "nearwhite_ratio", "bg_purity", "bg_largest_frac",
    "corner_bright", "corner_sat", "corner_consistency", "border_bright",
    "subject_area", "subject_bbox_max", "subject_centered", "subject_aspect",
    "subject_bg_dist", "subject_sat", "warmth_ab", "warmth_rb",
    "edge_density", "lap_var_256", "skin_ratio", "clip_white",
    "subject_margin_top", "subject_margin_bottom",
]

# 用于判别模型的特征：挑抗噪、跨品类稳定的量，避免小样本过拟合
MODEL_FEATURES = [
    "bright_mean", "bright_std", "contrast_rms", "sat_mean", "sat_p90",
    "colorfulness", "color_entropy", "white_ratio", "nearwhite_ratio",
    "bg_purity", "bg_largest_frac", "corner_bright", "corner_sat",
    "corner_consistency", "subject_area", "subject_bbox_max", "subject_centered",
    "subject_bg_dist", "subject_sat", "warmth_ab", "edge_density",
    "lap_var_256", "skin_ratio", "clip_white", "text_blob_frac", "bg_isolated_white",
]

# 人类可读的名称（报告里用）
NICE = {
    "bright_mean": "整体亮度", "bright_p50": "中间调亮度", "bright_std": "明暗起伏",
    "contrast_rms": "对比度", "contrast_range": "动态范围", "sat_mean": "平均饱和度",
    "sat_p90": "高饱和面积", "L_mean": "感知明度", "colorfulness": "色彩鲜艳度",
    "color_entropy": "配色复杂度", "white_ratio": "纯白底占比",
    "nearwhite_ratio": "近白底占比", "bg_purity": "背景纯度",
    "bg_largest_frac": "背景整片连通度", "bg_isolated_white": "孤立白块占比",
    "corner_bright": "四角亮度", "corner_sat": "四角饱和",
    "corner_consistency": "四角一致性", "border_bright": "边缘亮度",
    "subject_area": "主体面积占比", "subject_bbox_max": "主体最大跨度",
    "subject_centered": "主体居中程度", "subject_aspect": "主体宽高比",
    "subject_bg_dist": "主体与背景色差", "subject_sat": "主体饱和度",
    "warmth_ab": "色温(黄蓝)", "warmth_rb": "色温(红蓝)", "edge_density": "细节密度",
    "lap_var_256": "清晰度", "skin_ratio": "肤色占比", "clip_white": "过曝面积",
    "text_blob_frac": "笔画状纹理", "subject_margin_top": "上留白",
    "subject_margin_bottom": "下留白", "subject_margin_left": "左留白",
    "subject_margin_right": "右留白",
}


def nice(key: str) -> str:
    return NICE.get(key, key)


def _f(x, default=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def _clean_matrix(rows: list[dict], cols: list[str]) -> np.ndarray:
    return np.array([[_f(r.get(c)) for c in cols] for r in rows], dtype=np.float64)


# --------------------------------------------------------------------------
# 1. 口径校准
# --------------------------------------------------------------------------
def build_profile(approved: list[dict], rejected: list[dict], reference: list[dict],
                  min_approved: int = 5) -> dict:
    """从过审样本统计风格画像。"""
    prof: dict = {"version": 1}
    n_ok = len(approved)
    prof["n_approved"] = n_ok
    prof["n_rejected"] = len(rejected)
    prof["n_reference"] = len(reference)

    if n_ok < min_approved:
        prof["status"] = "insufficient"
        prof["note"] = (f"过审样本只有 {n_ok} 张（建议 ≥{min_approved} 张）。"
                        f"画像不可靠，本次评分以通用规则为主。")
        prof["stats"] = {}
        prof["weights"] = {"style": 0.0, "model": 0.0, "rule": 1.0}
        return prof

    prof["status"] = "ok"
    stats: dict[str, dict] = {}
    X = _clean_matrix(approved, STYLE_FEATURES)
    for j, c in enumerate(STYLE_FEATURES):
        col = X[:, j]
        med = float(np.median(col))
        # 稳健离散度：MAD 换算成 σ 量级。
        # 下限很关键：如果过审图彼此高度相似（比如全是同一套棚拍的图），离散度会趋近 0，
        # 于是任何轻微差异都被算成"好几个 σ 的偏离"，好图全被冤枉。
        # 实测教训：10 张样本建画像时，24 张真实网图有 14 张被打成"不建议"。
        mad = float(np.median(np.abs(col - med))) * 1.4826
        p10, p90 = np.percentile(col, [10, 90])
        iqr_scale = float((p90 - p10) / 2.563)
        floor = max(0.06, abs(med) * 0.15)
        scale = max(mad, iqr_scale, floor)
        stats[c] = {"median": round(med, 5), "scale": round(scale, 5),
                    "p10": round(float(p10), 5), "p90": round(float(p90), 5),
                    "n": int(col.size)}
    prof["stats"] = stats

    # 画像一致性：过审图之间彼此有多像（分块指纹的离散度）
    fp_cols = [f"fp{i}{j}" for i in range(6) for j in range(6)]
    fp = _clean_matrix(approved, fp_cols)
    prof["style_cohesion"] = round(float(1.0 / (1.0 + fp.std(axis=0).mean() * 6.0)), 4)
    # 过审图内部是否为统一白底风格
    wr = np.array([_f(r.get("white_ratio")) for r in approved])
    prof["white_bg_share"] = round(float((wr >= 0.5).mean()), 4)
    prof["dominant_bg"] = ("white" if prof["white_bg_share"] >= 0.6 else
                           "scene" if prof["white_bg_share"] <= 0.25 else "mixed")
    prof["real_size_sample"] = [
        {"w": int(_f(r.get("width"))), "h": int(_f(r.get("height")))} for r in approved[:50]]

    # 权重
    w = {"style": 1.0, "model": 0.0, "rule": 1.0}
    if len(rejected) >= max(4, int(0.6 * n_ok)):
        w["model"] = 1.0
    prof["weights"] = w
    return prof


# --------------------------------------------------------------------------
# 2. 逻辑回归（纯 numpy）
# --------------------------------------------------------------------------
class Logit:
    """带 L2 正则、类别平衡、特征标准化的二分类逻辑回归。"""

    def __init__(self, l2: float = 1.0, iters: int = 3000, lr: float = 0.6):
        self.l2, self.iters, self.lr = l2, iters, lr
        self.mu = None
        self.sd = None
        self.w = None
        self.b = 0.0
        self.cols: list[str] = []
        self.class_balance = 1.0
        self.train_acc = 0.0
        self.loo_acc = None
        self.loo_auc = None

    def _prep(self, rows: list[dict]) -> np.ndarray:
        X = _clean_matrix(rows, self.cols)
        return (X - self.mu) / self.sd

    def fit(self, pos: list[dict], neg: list[dict], cols: list[str],
            l2: float | None = None, iters: int | None = None, lr: float | None = None,
            compute_loo: bool = True) -> "Logit":
        self.cols = cols
        l2 = self.l2 if l2 is None else l2
        iters = self.iters if iters is None else iters
        lr = self.lr if lr is None else lr

        X = _clean_matrix(pos + neg, cols)
        y = np.array([1.0] * len(pos) + [0.0] * len(neg))
        self.mu = X.mean(axis=0)
        self.sd = np.maximum(X.std(axis=0), 1e-3)

        n_pos = max(1, len(pos)); n_neg = max(1, len(neg))
        self.class_balance = n_neg / n_pos
        sw = np.where(y > 0.5, self.class_balance, 1.0)
        sw = sw / sw.mean()

        self.w, self.b, self.train_acc = self._solve(X, y, sw, l2, iters, lr)
        if compute_loo and len(pos) >= 4 and len(neg) >= 4:
            self.loo_acc, self.loo_auc = self._loo(pos, neg, l2, iters, lr)
        return self

    def _solve(self, X, y, sw, l2, iters, lr):
        Z = (X - self.mu) / self.sd
        n, d = Z.shape
        w = np.zeros(d); b = 0.0
        for _ in range(iters):
            z = Z @ w + b
            p = 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))
            g = (p - y) * sw
            gw = Z.T @ g / n + l2 * w / n
            gb = float(g.mean())
            step = lr / (1.0 + 0.002 * _)      # 缓慢衰减，保证小样本稳定
            w -= step * gw
            b -= step * gb
        p = 1.0 / (1.0 + np.exp(-np.clip(Z @ w + b, -30, 30)))
        acc = float(((p > 0.5) == (y > 0.5)).mean())
        return w, b, acc

    def _loo(self, pos, neg, l2, iters, lr):
        rows = pos + neg
        y = np.array([1.0] * len(pos) + [0.0] * len(neg))
        preds = np.zeros(len(rows))
        for i in range(len(rows)):
            tr = [r for j, r in enumerate(rows) if j != i]
            tr_y = np.delete(y, i)
            Xtr = _clean_matrix(tr, self.cols)
            mu = Xtr.mean(axis=0); sd = np.maximum(Xtr.std(axis=0), 1e-3)
            sw = np.where(tr_y > 0.5, self.class_balance, 1.0); sw = sw / sw.mean()
            Ztr = (Xtr - mu) / sd
            w = np.zeros(Ztr.shape[1]); b = 0.0
            for it in range(iters):
                p = 1.0 / (1.0 + np.exp(-np.clip(Ztr @ w + b, -30, 30)))
                g = (p - tr_y) * sw
                w -= (lr / (1.0 + 0.002 * it)) * (Ztr.T @ g / len(tr) + l2 * w / len(tr))
                b -= (lr / (1.0 + 0.002 * it)) * float(g.mean())
            xi = _clean_matrix([rows[i]], self.cols)[0]
            preds[i] = 1.0 / (1.0 + math.exp(-float(np.clip((xi - mu) / sd @ w + b, -30, 30))))
        acc = float(((preds > 0.5) == (y > 0.5)).mean())
        # AUC（Mann-Whitney U）
        pos_p, neg_p = preds[y > 0.5], preds[y < 0.5]
        if len(pos_p) and len(neg_p):
            cmp = (pos_p[:, None] > neg_p[None, :]).astype(np.float64)
            tie = (pos_p[:, None] == neg_p[None, :]).astype(np.float64) * 0.5
            auc = float((cmp + tie).mean())
        else:
            auc = None
        return acc, auc

    def proba(self, rows: list[dict]) -> np.ndarray:
        if self.w is None or not rows:
            return np.zeros(len(rows))
        Z = self._prep(rows)
        return 1.0 / (1.0 + np.exp(-np.clip(Z @ self.w + self.b, -30, 30)))

    def coef_table(self, top: int = 12) -> list[dict]:
        if self.w is None:
            return []
        order = np.argsort(-np.abs(self.w))
        out = []
        for i in order[:top]:
            out.append({"feature": self.cols[i], "name": nice(self.cols[i]),
                        "coef": round(float(self.w[i]), 4),
                        "direction": "过审更可能" if self.w[i] > 0 else "被刷更可能"})
        return out

    def to_json(self) -> dict:
        return {"cols": self.cols, "mu": [round(float(x), 6) for x in (self.mu if self.mu is not None else [])],
                "sd": [round(float(x), 6) for x in (self.sd if self.sd is not None else [])],
                "w": [round(float(x), 6) for x in (self.w if self.w is not None else [])],
                "b": round(float(self.b), 6), "class_balance": round(self.class_balance, 4),
                "train_acc": round(self.train_acc, 4),
                "loo_acc": None if self.loo_acc is None else round(self.loo_acc, 4),
                "loo_auc": None if self.loo_auc is None else round(self.loo_auc, 4),
                "coefs": self.coef_table()}

    @classmethod
    def from_json(cls, obj: dict) -> "Logit":
        m = cls()
        m.cols = list(obj.get("cols", []))
        m.mu = np.array(obj.get("mu", []), dtype=np.float64)
        m.sd = np.array(obj.get("sd", []), dtype=np.float64)
        m.w = np.array(obj.get("w", []), dtype=np.float64)
        m.b = float(obj.get("b", 0.0))
        m.train_acc = float(obj.get("train_acc", 0.0))
        m.loo_acc = obj.get("loo_acc")
        m.loo_auc = obj.get("loo_auc")
        return m


def profile_reliability(profile: dict) -> tuple[float, str]:
    """画像可信度 0~1：样本越多越可信。

    样本少时画像只是"这 10 张图碰巧长这样"，不该有强否决权。
    实测教训：10 张样本建的画像曾把 24 张真实网图里 14 张打成"不建议"。
    """
    n = int(profile.get("n_approved", 0) or 0)
    if not profile.get("stats") or n <= 0:
        return 0.0, "无画像"
    rel = min(1.0, max(0.0, (n - 4) / 26.0))     # 5 张 -> 0.04，30 张 -> 1.0
    # 过审图本身越统一，画像越有意义；太散说明"你的过审图没有统一风格"
    coh = float(profile.get("style_cohesion") or 0.5)
    rel *= 0.55 + 0.45 * min(1.0, max(0.0, coh))
    desc = f"过审样本 {n} 张，风格一致性 {coh:.2f}"
    return round(rel, 4), desc


def model_reliability(logit: Logit | None, n_pos: int, n_neg: int) -> tuple[float, str]:
    """模型可信度 0~1：样本量 + 交叉验证表现共同决定，样本太少直接压到 0.2。"""
    if logit is None or logit.w is None:
        return 0.0, "无模型"
    n = min(n_pos, n_neg)
    size_gate = min(1.0, max(0.0, (n - 3) / 12.0))       # 4 张 -> 0.08，15 张 -> 1.0
    perf = logit.loo_auc if logit.loo_auc is not None else (logit.loo_acc or 0.5)
    if logit.loo_auc is not None:
        perf_gate = min(1.0, max(0.0, (perf - 0.55) / 0.30))   # AUC .55->0, .85->1
    else:
        perf_gate = min(1.0, max(0.0, (perf - 0.60) / 0.30))
    rel = size_gate * (0.35 + 0.65 * perf_gate)
    if n < 6:
        rel = min(rel, 0.25)
    desc = f"样本 {n_pos}过审/{n_neg}被刷"
    if logit.loo_auc is not None:
        desc += f"，留一交叉验证 AUC={logit.loo_auc:.2f}"
    elif logit.loo_acc is not None:
        desc += f"，留一准确率={logit.loo_acc:.2f}"
    else:
        desc += "，样本不足未做交叉验证"
    return round(rel, 4), desc
