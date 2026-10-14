# -*- coding: utf-8 -*-
"""
织物证据检测 —— 区分"平铺设计稿"和"织物实拍"。

为什么需要它
------------
用户 8 张已上架图里，7.jpg（骷髅南瓜）和 8.png（粉调鬼魂）肉眼能看出明显的
布料起伏波浪和褶皱，是**拍出来的毯面照片**，不是平铺设计稿。
但"无缝图案"的周期性判据对它们失效（周期相关只有 0.43~0.51），
导致自动分型把它们判成图案稿 —— 因为判据里根本没有"织物"这个维度。

织物证据怎么算
--------------
织物照片有三个平铺设计稿不会有的特征：
1. **大尺度明暗起伏**：布料波浪在低频上产生宽缓的明暗渐变。
   做法：重度模糊后取明度的标准差（低频不均匀度）。
2. **褶皱脊线**：褶皱是细长的亮/暗带，方向一致。
   做法：模糊后的梯度能量，以及梯度的方向一致性。
3. **局部对比的随机性**：织物质感让局部对比忽高忽低，设计稿则很均匀。
   做法：分块局部标准差的离散度。

平铺设计稿这三个指标都会很低（色块平坦、无方向性、对比均匀）。
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

WORK = 384


def _gray_arr(path) -> np.ndarray | None:
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > WORK:
                s = WORK / max(im.size)
                im = im.resize((max(16, int(im.width * s)), max(16, int(im.height * s))),
                               Image.LANCZOS)
            a = np.asarray(im, dtype=np.float32) / 255.0
        return a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    except Exception:
        return None


def fabric_evidence(path) -> dict:
    """返回织物质感指标。数值越高越像"织物实拍"。"""
    g = _gray_arr(path)
    if g is None:
        return {"ok": False}
    h, w = g.shape

    # 1) 低频起伏：重度模糊后的明度标准差
    g8 = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8), "L")
    k = max(5, (min(h, w) // 12) | 1)
    blur = np.asarray(g8.filter(ImageFilter.GaussianBlur(radius=k)), dtype=np.float32) / 255.0
    lowfreq_std = float(blur.std())

    # 2) 褶皱脊线：模糊图的梯度能量 + 方向一致性
    bx = np.zeros_like(blur); by = np.zeros_like(blur)
    bx[:, 1:-1] = blur[:, 2:] - blur[:, :-2]
    by[1:-1, :] = blur[2:, :] - blur[:-2, :]
    ridge = float(np.sqrt(bx * bx + by * by).mean())
    # 方向一致性：结构张量的各向异性
    jxx = float((bx * bx).mean()); jyy = float((by * by).mean()); jxy = float((bx * by).mean())
    tr = jxx + jyy
    det = jxx * jyy - jxy * jxy
    disc = max(0.0, tr * tr / 4.0 - det)
    l1 = tr / 2.0 + np.sqrt(disc)
    l2 = tr / 2.0 - np.sqrt(disc)
    anisotropy = float((l1 - l2) / (l1 + l2 + 1e-9))

    # 3) 分块局部标准差的离散度
    bs = max(8, min(h, w) // 8)
    ny, nx = h // bs, w // bs
    if ny >= 2 and nx >= 2:
        blocks = g[:ny * bs, :nx * bs].reshape(ny, bs, nx, bs).std(axis=(1, 3))
        block_std_cv = float(blocks.std() / (blocks.mean() + 1e-6))
    else:
        block_std_cv = 0.0

    return {
        "ok": True,
        "lowfreq_std": round(lowfreq_std, 5),
        "ridge_energy": round(ridge, 5),
        "anisotropy": round(anisotropy, 4),
        "block_std_cv": round(block_std_cv, 4),
    }


def fabric_score(ev: dict) -> float:
    """把织物质感指标压成 0~1 的分数（越高越像织物实拍）。"""
    if not ev.get("ok"):
        return 0.0
    lf = min(1.0, max(0.0, (ev["lowfreq_std"] - 0.02) / 0.09))
    rg = min(1.0, max(0.0, (ev["ridge_energy"] - 0.004) / 0.020))
    cv = min(1.0, max(0.0, (ev["block_std_cv"] - 0.15) / 0.60))
    return round(0.45 * lf + 0.35 * rg + 0.20 * cv, 4)


TYPE_ZH2 = {
    "design_flat": "平铺设计稿",
    "fabric_photo": "毯子实拍/织物图",
    "unknown": "无法确定",
}
