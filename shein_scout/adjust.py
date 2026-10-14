# -*- coding: utf-8 -*-
"""
shein_scout.adjust —— 可执行的调色建议与"预览图"生成

为什么需要它
------------
实测发现（5/5 成功文件夹一致）：成功上架的图饱和度显著更低
    sat_mean      成功 0.198  vs  被刷 0.327
    sat_p90       成功 0.497  vs  被刷 0.822
    colorfulness  成功 0.139  vs  被刷 0.229

但只报"饱和度偏高"没用 —— 用户要知道**降多少、怎么降**。
所以这里直接生成降饱和后的预览图，让他看完再决定，而不是盲猜参数。

实现要点
--------
* 用 PIL 的 HSV 模式做饱和度缩放：H/S/V 都是 0~255，S 直接乘系数即可。
* 注意 H 通道是**循环量**，缩放全部三个通道会把色相也搅乱，所以逐通道处理。
* 预览图带尺寸上限，避免报告体积爆炸。
"""
from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except Exception:
        return d


def sat_scale(path: str | Path, factor: float) -> Image.Image | None:
    """返回饱和度乘以 factor 后的图像（色相/明度不变）。factor<1 = 降饱和。"""
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            hsv = np.asarray(im.convert("HSV"), dtype=np.uint8).astype(np.float32)
            hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 255)
            out = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")
        return out
    except Exception:
        return None


def png_bytes(im: Image.Image, max_side: int = 720, quality: int = 80,
              fmt: str = "JPEG") -> bytes:
    out = im
    if max(out.size) > max_side:
        s = max_side / max(out.size)
        out = out.resize((max(1, int(out.width * s)), max(1, int(out.height * s))),
                         Image.LANCZOS)
    buf = io.BytesIO()
    if fmt == "JPEG":
        out.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    else:
        out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def target_factor(current_sat: float, want_sat: float, floor: float = 0.35) -> float:
    """算出把当前饱和度拉到目标值所需的系数。floor 防止给出"几乎去色"的极端建议。"""
    cur, want = _f(current_sat), _f(want_sat)
    if cur <= 1e-6 or cur <= want:
        return 1.0
    return max(floor, min(1.0, want / cur))


def load_thresholds(root: str | Path | None = None) -> dict:
    """载入用成功上架图校准出来的颜色阈值（work/color_thresholds.json）。"""
    import json
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    fp = base / "work" / "color_thresholds.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# 默认阈值（在拿不到校准文件时使用；数值取自实测成功组的 p75）
FALLBACK_THRESHOLDS = {
    "sat_mean": {"threshold_p75": 0.2993, "over_rate_approved": 0.26},
    "sat_p90": {"threshold_p75": 0.6985, "over_rate_approved": 0.26},
    "colorfulness": {"threshold_p75": 0.1901, "over_rate_approved": 0.26},
}


def load_thresholds(root: str | Path | None = None) -> dict:
    """载入用成功上架图校准出来的颜色阈值（work/color_thresholds.json）。"""
    import json
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    fp = base / "work" / "color_thresholds.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# 拿不到校准文件时的兜底（数值取自实测成功组的 p75）
FALLBACK_THRESHOLDS = {
    "sat_mean": {"threshold_p75": 0.2993, "over_rate_approved": 0.26},
    "sat_p90": {"threshold_p75": 0.6985, "over_rate_approved": 0.26},
    "colorfulness": {"threshold_p75": 0.1901, "over_rate_approved": 0.26},
}


def plan(feat: dict, profile: dict, max_variants: int = 2,
         thresholds: dict | None = None) -> dict:
    """给出降饱和方案。

    重要设计说明（两次踩坑记录）：
      1) 一开始用成功组**中位数**当阈值 -> 按定义一半成功图会超标，已成功的图也被判
         "需要降饱和"。假警报。改用 **p75**。
      2) 又发现拿鲜艳度阈值反推饱和度目标会错位（鲜艳度 ≈ 饱和度 × 色相多样性，
         同降 20% 饱和度鲜艳度降得更多），导致"饱和度完全正常却建议降 12%"。
         改为一套统一的缩放系数，并在报告里给出降完后各指标的预估落点。
    """
    th = thresholds if thresholds is not None else load_thresholds()
    stats = (profile or {}).get("stats") or {}
    out = {
        "needed": False, "factors": [], "reasons": [], "projection": {},
        "current": round(_f(feat.get("sat_mean")), 4),
        "current_colorfulness": round(_f(feat.get("colorfulness")), 4),
        "current_sat_p90": round(_f(feat.get("sat_p90")), 4),
        "target": None, "baseline_note": "", "confidence": "",
    }
    if not stats:
        out["reasons"].append("没有风格画像，无法给出调色建议")
        return out

    def thr(metric: str) -> tuple[float, float]:
        d = th.get(metric) or FALLBACK_THRESHOLDS.get(metric) or {}
        # 优先读 threshold（已校准为成功组 p85）；兼容早期的 threshold_p75
        t = _f(d.get("threshold"), 0.0) or _f(d.get("threshold_p75"), 0.0)
        rate = _f(d.get("over_rate_approved"), 0.0)
        if t <= 0:
            sm = stats.get(metric) or {}
            t = _f(sm.get("p90"), 0.0) or _f(sm.get("median"), 0.0) * 1.5
            rate = 0.0
        return t, rate

    t_sat, r_sat = thr("sat_mean")
    t_cf, r_cf = thr("colorfulness")
    t_p90, r_p90 = thr("sat_p90")

    cur_sat = _f(feat.get("sat_mean"))
    cur_cf = _f(feat.get("colorfulness"))
    cur_p90 = _f(feat.get("sat_p90"))

    # 各指标"需要缩到多少"的比例；彩度类指标按 sqrt 折算（鲜艳度对饱和度的响应是平方量级）
    # 安全余量：实测标定显示三个指标对降幅都是**线性**响应（偏差 ≤6%，来自 JPEG 重编码）。
    # 闭环验证曾出现刚好卡阈值却轻微超标（实测 0.3352 vs 阈值 0.3320），所以留 8% 余量。
    MARGIN = 0.92
    cuts = []
    if t_sat > 0 and cur_sat > t_sat:
        cuts.append(("sat_mean", "平均饱和度", cur_sat, t_sat, r_sat,
                     (t_sat / cur_sat) * MARGIN))
    if t_cf > 0 and cur_cf > t_cf:
        cuts.append(("colorfulness", "色彩鲜艳度", cur_cf, t_cf, r_cf,
                     (t_cf / cur_cf) * MARGIN))
    if t_p90 > 0 and cur_p90 > t_p90:
        cuts.append(("sat_p90", "高饱和区域占比", cur_p90, t_p90, r_p90,
                     (t_p90 / cur_p90) * MARGIN))

    if not cuts:
        out["reasons"].append("色彩饱和度处于成功上架图的正常范围内，无需调整")
        out["target"] = round(t_sat, 4)
        return out

    raw_factor = max(0.35, min(o[5] for o in cuts))
    # 最小有效降幅：低于 5% 的调整在实物上几乎看不出，报了只会增加噪音。
    # 实测踩坑：曾给出"降 3%"的建议，用户无法执行也无意义。
    if raw_factor > 0.95:
        out["reasons"].append(
            f"色彩略高于成功图的常见上限，但差距很小（需降 {int(round((1 - raw_factor) * 100))}%），"
            f"实际影响可忽略，无需处理")
        out["target"] = round(t_sat, 4)
        return out
    out["needed"] = True
    factor = raw_factor   # 取最保守（缩得最多）的那个
    f1 = round(factor, 3)
    factors = [f1]
    if max_variants >= 2:
        # 温和档取"强档与 1 的中点"，并强制拉开至少 8 个百分点 ——
        # 差距太小的话两格预览看不出区别，不如只给一档。
        f2 = round(min(1.0, (f1 + 1.0) / 2.0), 3)
        if f2 < 0.995 and (f2 - f1) >= 0.08:
            factors.append(f2)
    out["factors"] = sorted(set(factors))
    out["target"] = round(t_sat, 4)

    for _, name, val, t, rate, _c in cuts:
        txt = f"{name} {val:.3f} 高于成功上架图的上限（p85 = {t:.3f}）"
        if rate > 0:
            txt += f"（约 {rate:.0%} 的成功上架图也超过此阈值，属风险提示而非硬门槛）"
        out["reasons"].append(txt)

    # 降完后各指标的预估落点，供用户判断"够不够"
    # 预估落点：实测标定确认三者都是线性响应，所以统一乘以系数 f1。
    # （早期版本给鲜艳度用 f²、给高饱和占比用 f^0.7，是拍脑袋的假设，已被实测推翻。）
    out["projection"] = {
        "sat_mean": {"now": round(cur_sat, 4), "after": round(cur_sat * f1, 4),
                     "threshold": round(t_sat, 4)},
        "colorfulness": {"now": round(cur_cf, 4), "after": round(cur_cf * f1, 4),
                         "threshold": round(t_cf, 4)},
        "sat_p90": {"now": round(cur_p90, 4), "after": round(cur_p90 * f1, 4),
                    "threshold": round(t_p90, 4)},
    }
    out["baseline_note"] = ("阈值来自你 50 张成功上架图的分布（p85，即最艳的那 15% 之外）。"
                            "它标记的是'比大多数成功图更艳'，不是'一定被刷'。")
    out["confidence"] = "中" if len(cuts) >= 2 else "偏低"
    return out


def make_previews(path: str | Path, factors: list[float],
                  max_side: int = 720, quality: int = 80) -> dict[float, bytes]:
    """为每个系数生成降饱和预览（返回 {factor: jpeg bytes}）。"""
    res: dict[float, bytes] = {}
    for f in factors:
        if abs(f - 1.0) < 1e-6:
            continue
        im = sat_scale(path, f)
        if im is None:
            continue
        res[f] = png_bytes(im, max_side=max_side, quality=quality)
    return res


def apply_to_file(src: str | Path, dst: str | Path, factor: float) -> Path | None:
    """把降饱和结果落盘（用于批量修图）。"""
    im = sat_scale(src, factor)
    if im is None:
        return None
    d = Path(dst)
    d.parent.mkdir(parents=True, exist_ok=True)
    if d.suffix.lower() in (".jpg", ".jpeg"):
        im.save(d, "JPEG", quality=95, subsampling=0)
    else:
        im.save(d, "PNG", optimize=True)
    return d
