# -*- coding: utf-8 -*-
"""
shein_scout.imagetype —— 判断一张图是"图案设计稿"还是"商品展示图"

为什么必须分开
--------------
实测用户已上架的 8 张图，现有规则平均扣 28.8 分 —— 因为规则默认"图里有个中心主体"。
但印花类目里，**图案平铺文件根本没有中心主体**，于是：
  * subject_area 判 0.7%~5.8% -> 误扣"主体太小"
  * color_entropy 天然偏高     -> 误扣"配色杂乱"
  * 图案铺满整幅               -> 误扣"非白底"
全都是把产品特性当成缺陷。所以先分型，再按型套不同判据。

判据设计（尽量用物理特征，不靠玄学）
------------------------------------
1. **平铺周期性**：无缝图案文件是设计成可重复拼接的，所以把图平移若干像素后
   与原图重合度会异常高。真实照片不会有这种精确周期性。
   用"中心裁剪 vs 整体"的归一化相关来算，取横向/纵向的最佳匹配位移。
2. **调色板规模**：矢量图案的颜色数通常很少（十几到几十种），
   而照片有成千上万种过渡色。
3. **边缘梯度**：矢量图是硬边（梯度极大值集中），照片是软过渡。
4. **主体占比**：展示图里有明确的商品主体。
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

WORK = 256


def _prep(path) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > WORK:
                s = WORK / max(im.size)
                im = im.resize((max(16, int(im.width * s)), max(16, int(im.height * s))),
                               Image.LANCZOS)
            return np.asarray(im, dtype=np.float32) / 255.0
    except Exception:
        return None


def tile_periodicity(arr: np.ndarray, max_shift: float = 0.55) -> tuple[float, float, float]:
    """返回 (最佳横向相关, 最佳纵向相关, 最佳位移比例)。

    做法：把图按位移 d 平移后与自身比较，取重叠区做归一化相关。
    无缝图案在 d ≈ 重复周期 处会出现明显峰值。
    """
    h, w = arr.shape[:2]
    gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gray = gray - gray.mean()
    denom0 = float(np.sqrt((gray * gray).sum())) or 1.0

    def best_corr(axis: int) -> tuple[float, float]:
        best, best_d = 0.0, 0.0
        limit = int((h if axis == 0 else w) * max_shift)
        for d in range(4, max(5, limit)):
            if axis == 0:
                a, b = gray[:-d, :], gray[d:, :]
            else:
                a, b = gray[:, :-d], gray[:, d:]
            if a.size < 64:
                break
            an = a - a.mean()
            bn = b - b.mean()
            den = float(np.sqrt((an * an).sum()) * np.sqrt((bn * bn).sum()))
            if den <= 1e-6:
                continue
            c = float((an * bn).sum()) / den
            if c > best:
                best, best_d = c, d / float(h if axis == 0 else w)
        return best, best_d

    cy, dy = best_corr(0)
    cx, dx = best_corr(1)
    return cy, cx, max(dy, dx)


def palette_size(arr: np.ndarray, tol: int = 12) -> int:
    """量化后的颜色种类数（近似调色板规模）。"""
    q = (arr.reshape(-1, 3) * 255).astype(np.uint8)
    q = (q // tol) * tol
    return int(len(np.unique(q, axis=0)))


def flatness(arr: np.ndarray) -> tuple[float, float]:
    """返回 (硬边比例, 平坦区比例)：矢量图硬边多、平坦色块多。"""
    g = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:-1] = g[:, 2:] - g[:, :-2]
    gy[1:-1, :] = g[2:, :] - g[:-2, :]
    mag = np.sqrt(gx * gx + gy * gy)
    hard = float((mag > 0.25).mean())          # 硬边：相邻像素差很大
    flat = float((mag < 0.01).mean())          # 平坦：几乎没有梯度
    return hard, flat


def classify(path, feat: dict | None = None) -> dict:
    """判断图型。返回 {type, confidence, evidence, metrics}。

    type: pattern_flat（图案设计稿）| mockup（商品展示图）| photo（实拍/场景照）| unknown
    """
    arr = _prep(path)
    if arr is None:
        return {"type": "unknown", "confidence": 0.0, "evidence": ["无法读取"], "metrics": {}}

    cy, cx, shift = tile_periodicity(arr)
    pal = palette_size(arr)
    hard, flat = flatness(arr)
    h, w = arr.shape[:2]
    aspect = w / max(1, h)

    subj = float((feat or {}).get("subject_area") or 0.0)
    entropy = float((feat or {}).get("color_entropy") or 0.0)
    white = float((feat or {}).get("white_ratio") or 0.0)
    skin = float((feat or {}).get("skin_ratio") or 0.0)

    metrics = {
        "tile_corr_v": round(cy, 4), "tile_corr_h": round(cx, 4),
        "tile_shift": round(shift, 4), "palette": pal,
        "hard_edge": round(hard, 4), "flat_area": round(flat, 4),
        "aspect": round(aspect, 4), "subject_area": round(subj, 4),
        "color_entropy": round(entropy, 4),
    }

    ev: list[str] = []
    # ---- 图案设计稿的判据 ----
    pattern_votes = 0.0
    if cy > 0.80 and cx > 0.80:
        pattern_votes += 1.4
        ev.append(f"画面在横纵两个方向都能与自身高度重合（相关 {cy:.2f}/{cx:.2f}）"
                  f"——这是无缝平铺图案的特征")
    elif max(cy, cx) > 0.85:
        pattern_votes += 0.7
        ev.append(f"存在强周期性（最佳相关 {max(cy, cx):.2f}）")
    if pal <= 3000 and hard > 0.02:
        pattern_votes += 0.8
        ev.append(f"调色板只有约 {pal} 种颜色且硬边占比 {hard:.1%}——矢量图特征")
    if flat > 0.45:
        pattern_votes += 0.5
        ev.append(f"近半画面（{flat:.0%}）是零梯度平坦色块")
    if subj < 0.12:
        pattern_votes += 0.7
        ev.append(f"没有中心主体（最大前景块仅占 {subj:.1%}），符合平铺图案")
    if white < 0.2 and entropy > 0.15:
        pattern_votes += 0.3

    # ---- 展示图的判据 ----
    # subj 门槛取 0.55：真实商品展示图里商品要占据画面主体（实测 mockup 为 0.83），
    # 而印花图案即使有密集元素，其"最大前景块"也到不了半数（实测 0.45）。
    # 取 0.35 时万圣节图案图会被误判成展示图。
    mockup_votes = 0.0
    if subj > 0.55:
        mockup_votes += 1.4
        ev.append(f"有明确商品主体（占画面 {subj:.0%}）")
    elif subj > 0.35:
        mockup_votes += 0.4
        ev.append(f"有较大前景块（占画面 {subj:.0%}），但未达实物展示的典型占比")
    if white > 0.35:
        mockup_votes += 0.9
        ev.append(f"白底/浅底占比 {white:.0%}，符合商品展示图")
    if not (cy > 0.80 and cx > 0.80):
        mockup_votes += 0.5
    if pal > 8000:
        mockup_votes += 0.6
        ev.append(f"颜色过渡丰富（约 {pal} 种色），是照片而非矢量图")
    if subj > 0.55 and skin > 0.02:
        mockup_votes += 0.8
        ev.append(f"检出肤色，画面里有真人模特（占 {skin:.0%}）")

    if mockup_votes > pattern_votes + 0.4:
        t, conf = "mockup", min(1.0, (mockup_votes - pattern_votes) / 2.5)
    elif pattern_votes > mockup_votes + 0.4:
        t, conf = "pattern_flat", min(1.0, (pattern_votes - mockup_votes) / 2.5)
    else:
        t, conf = "unknown", 0.2
        ev.append("两类特征接近，无法确定图型")

    return {"type": t, "confidence": round(conf, 3),
            "evidence": ev, "metrics": metrics,
            "votes": {"pattern": round(pattern_votes, 2), "mockup": round(mockup_votes, 2)}}


TYPE_ZH = {
    "pattern_flat": "图案设计稿",
    "mockup": "商品展示图",
    "photo": "实拍/场景照",
    "unknown": "无法确定",
}
