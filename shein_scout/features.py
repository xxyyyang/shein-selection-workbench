# -*- coding: utf-8 -*-
"""
shein_scout.features —— 图片特征提取引擎

设计目标
--------
1. 只依赖 numpy + Pillow（这台机器上开箱即用），不依赖 torch / cv2 / sklearn。
2. 输出**可解释**的特征，让"为什么这张图被判不合格"能直接落到具体数值上，
   而不是一个黑盒分数。所有特征都能写进报告给你复查。
3. 速度快：单图 512px 工作分辨率，单张约 0.1~0.3 秒。

特征分五组
----------
A. 规格类   : 尺寸、宽高比、是否方形、像素量、清晰度
B. 影调色彩 : 亮度、饱和度、对比度、色温、鲜艳度、过曝/死黑
C. 背景与主体: 白底占比、背景纯度、主体面积/居中/出血、主体与背景色差
D. 画面干扰 : 文字/水印嫌疑、杂色熵、重复纹理
E. 风格指纹 : RGB 分块网格均值（用于风格相似度与查重）
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

Image.MAX_IMAGE_PIXELS = 300_000_000  # 允许大图，但不至于吃爆内存

WORK = 512          # 特征计算工作分辨率（长边）
GRID = 6            # 风格指纹网格 GRID x GRID
FMT_VERSION = 1     # 特征版本：公式变了就 +1，用于让缓存失效


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """rgb: (...,3) in [0,1] -> hsv in [0,1]，向量化实现。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.max(rgb, axis=-1)
    mn = np.min(rgb, axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    nz = d > 1e-8
    # 三个分支
    idx = nz & (mx == r)
    h[idx] = ((g[idx] - b[idx]) / d[idx]) % 6.0
    idx = nz & (mx == g)
    h[idx] = (b[idx] - r[idx]) / d[idx] + 2.0
    idx = nz & (mx == b)
    h[idx] = (r[idx] - g[idx]) / d[idx] + 4.0
    h = (h / 6.0) % 1.0
    s = np.where(mx > 1e-8, d / np.maximum(mx, 1e-8), 0.0)
    return np.stack([h, s, mx], axis=-1)


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB [0,1] -> CIELAB。仅用 L 和 ab 的近似色度，足够做风格判断。"""
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float64)
    xyz = lin @ m.T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    eps, kap = 216 / 24389, 24389 / 27
    f = np.where(xyz > eps, np.cbrt(xyz), (kap * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _lap_var(gray: np.ndarray, size: int = 512) -> float:
    """拉普拉斯方差（清晰度）。按图像尺寸归一化，避免大图天然分高。"""
    g = Image.fromarray((np.clip(gray, 0, 1) * 255).astype(np.uint8), "L")
    lap = np.asarray(g.filter(ImageFilter.FIND_EDGES), dtype=np.float32) / 255.0
    return float(lap.var() * 1e4)


def _tenengrad(gray: np.ndarray) -> float:
    """Tenengrad 清晰度：梯度能量均值，抗噪性比拉普拉斯方差好。"""
    gx = np.diff(gray, axis=1, prepend=gray[:, :1])
    gy = np.diff(gray, axis=0, prepend=gray[:1, :])
    return float(np.mean(gx * gx + gy * gy) * 1e4)


def _label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """两遍扫描 + 并查集的连通域标记（4 邻域）。mask: bool 2D。

    性能要点：先裁到前景外接框再做逐像素扫描。原实现对整个 512px 画布
    做 Python 级循环，单次调用就要几百毫秒；裁剪后通常快一个数量级。
    """
    H, W = mask.shape
    lab = np.zeros((H, W), dtype=np.int32)
    rows_any = mask.any(axis=1)
    if not rows_any.any():
        return lab, 0
    y0, y1 = int(np.argmax(rows_any)), int(H - np.argmax(rows_any[::-1]))
    cols_any = mask.any(axis=0)
    x0, x1 = int(np.argmax(cols_any)), int(W - np.argmax(cols_any[::-1]))

    sub = mask[y0:y1, x0:x1]
    h, w = sub.shape
    slab = np.zeros((h, w), dtype=np.int32)
    parent: dict[int, int] = {0: 0}
    nxt = 1

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    for y in range(h):
        row = sub[y]
        if not row.any():
            continue
        px = slab[y - 1] if y > 0 else None
        for x in np.nonzero(row)[0]:
            x = int(x)
            up = int(px[x]) if px is not None else 0
            lf = int(slab[y, x - 1]) if x > 0 else 0
            if up and lf:
                slab[y, x] = up
                if up != lf:
                    ra, rb = find(up), find(lf)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
            elif up:
                slab[y, x] = up
            elif lf:
                slab[y, x] = lf
            else:
                slab[y, x] = nxt
                parent[nxt] = nxt
                nxt += 1

    if nxt == 1:
        return lab, 0
    # 把并查集根映射成紧凑编号
    roots = {}
    out = np.zeros_like(slab)
    for i in np.unique(slab):
        i = int(i)
        if i == 0:
            continue
        r = find(i)
        if r not in roots:
            roots[r] = len(roots) + 1
        out[slab == i] = roots[r]
    lab[y0:y1, x0:x1] = out
    return lab, len(roots)


def _dilate(mask: np.ndarray, it: int = 1) -> np.ndarray:
    """3x3 膨胀，三角边界。"""
    out = mask
    for _ in range(it):
        p = np.pad(out, 1, mode="edge")
        out = (p[0:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, 0:-2] | p[1:-1, 2:] |
               p[1:-1, 1:-1] | p[0:-2, 0:-2] | p[0:-2, 2:] | p[2:, 0:-2] | p[2:, 2:])
    return out


def _erode(mask: np.ndarray, it: int = 1) -> np.ndarray:
    out = mask
    for _ in range(it):
        p = np.pad(out, 1, mode="edge")
        out = (p[0:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, 0:-2] & p[1:-1, 2:] &
               p[1:-1, 1:-1] & p[0:-2, 0:-2] & p[0:-2, 2:] & p[2:, 0:-2] & p[2:, 2:])
    return out


def _pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q))


def _round(x: float, n: int = 4) -> float:
    try:
        v = float(x)
    except Exception:
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return round(v, n)


# --------------------------------------------------------------------------
# 加载
# --------------------------------------------------------------------------
def load_image(path: str | Path) -> tuple[np.ndarray, dict]:
    """读图 -> 合成到白底 -> 应用 EXIF 方向。返回 (uint8 HWC RGB, 元信息)。"""
    p = Path(path)
    with Image.open(p) as im:
        meta = {
            "orig_format": im.format or "",
            "orig_mode": im.mode,
            "orig_w": im.width,
            "orig_h": im.height,
            "has_alpha": "A" in im.mode,
            "exif_orientation": None,
        }
        try:
            ex = im.getexif()
            meta["exif_orientation"] = ex.get(274)
        except Exception:
            pass
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            im = Image.alpha_composite(bg, rgba).convert("RGB")
        elif im.mode != "RGB":
            im = im.convert("RGB")
        arr = np.asarray(im, dtype=np.uint8)
    return arr, meta


# --------------------------------------------------------------------------
# 主特征函数
# --------------------------------------------------------------------------
def extract(path: str | Path) -> dict | None:
    """提取单张图片的全部特征。失败返回 None（由调用方记录错误）。"""
    p = Path(path)
    try:
        full, meta = load_image(p)
    except Exception as e:  # 坏图 / 不支持的格式
        return {"file": str(p), "name": p.name, "ok": False,
                "error": f"{type(e).__name__}: {e}"}

    H, W = full.shape[:2]
    if H < 8 or W < 8:
        return {"file": str(p), "name": p.name, "ok": False, "error": "图片过小"}

    rgb_full = full.astype(np.float32) / 255.0
    # 工作分辨率
    scale = WORK / max(H, W)
    ww, wh = max(8, int(round(W * scale))), max(8, int(round(H * scale)))
    small = np.asarray(
        Image.fromarray(full).resize((ww, wh), Image.LANCZOS), dtype=np.float32
    ) / 255.0

    gray_s = small @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    hsv = _rgb_to_hsv(small)
    lab = _rgb_to_lab(small)

    f: dict = {
        "file": str(p),
        "name": p.name,
        "ok": True,
        "error": "",
        "fmt_version": FMT_VERSION,
    }
    try:
        st = p.stat()
        f["bytes"] = int(st.st_size)
        f["mtime"] = float(st.st_mtime)
        f["md5_16"] = hashlib.md5(
            f"{p.name}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    except Exception:
        f["bytes"] = 0
        f["mtime"] = 0.0
        f["md5_16"] = ""

    # ---------- A. 规格 ----------
    f.update(meta)
    f.update({
        "width": int(W), "height": int(H),
        "pixels": int(W * H),
        "aspect": _round(W / H, 5),
        "ar_1_1": _round(min(W, H) / max(W, H), 5),
        "is_square": bool(abs(W / H - 1.0) < 0.02),
        "is_portrait": bool(H > W * 1.02),
        "is_landscape": bool(W > H * 1.02),
        "min_side": int(min(W, H)),
        "megapixels": _round(W * H / 1e6, 3),
        "shot_scale": _round(max(W, H) / 1345.0, 3),
    })

    # 清晰度（在全分辨率上算）
    gf = rgb_full @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    f["lap_var"] = _round(_lap_var(gf), 3)
    f["tenengrad"] = _round(_tenengrad(gf), 3)
    # 清晰度做尺寸归一化对比：把长边统一缩到 1024 再算，避免大图天然占便宜
    if max(W, H) > 1024:
        sc2 = 1024.0 / max(W, H)
        gn = np.asarray(Image.fromarray((gf * 255).astype(np.uint8), "L")
                        .resize((max(8, int(W * sc2)), max(8, int(H * sc2))), Image.LANCZOS),
                        dtype=np.float32) / 255.0
    else:
        gn = gf
    f["lap_var_1024"] = _round(_lap_var(gn), 3)
    f["tenengrad_1024"] = _round(_tenengrad(gn), 3)
    if max(W, H) > 800:  # 下采样后仍清晰 = 真的清晰
        gs2 = np.asarray(Image.fromarray((gf * 255).astype(np.uint8), "L")
                         .resize((256, 256), Image.BILINEAR), dtype=np.float32) / 255.0
        f["lap_var_256"] = _round(_lap_var(gs2), 3)
    else:
        f["lap_var_256"] = f["lap_var"]
    del gf, gn

    # ---------- B. 影调色彩 ----------
    L = lab[..., 0] / 100.0
    S = hsv[..., 1]
    V = hsv[..., 2]
    f.update({
        "bright_mean": _round(gray_s.mean()),
        "bright_std": _round(gray_s.std()),
        "bright_p05": _round(_pct(gray_s, 5)),
        "bright_p50": _round(_pct(gray_s, 50)),
        "bright_p95": _round(_pct(gray_s, 95)),
        "L_mean": _round(L.mean()),
        "L_std": _round(L.std()),
        "contrast_rms": _round(float(np.sqrt(np.mean((L - L.mean()) ** 2)))),
        "contrast_range": _round(_pct(L, 95) - _pct(L, 5)),
        "sat_mean": _round(S.mean()),
        "sat_std": _round(S.std()),
        "sat_p90": _round(_pct(S, 90)),
        "val_mean": _round(V.mean()),
        "warmth_ab": _round(lab[..., 2].mean()),
        "warmth_rb": _round(small[..., 0].mean() - small[..., 2].mean()),
        "green_magenta": _round(lab[..., 1].mean()),
        "clip_white": _round(float((gray_s > 0.98).mean())),
        "clip_black": _round(float((gray_s < 0.02).mean())),
    })

    # Hasler-Susstrunk 鲜艳度
    rg = small[..., 0] - small[..., 1]
    yb = 0.5 * (small[..., 0] + small[..., 1]) - small[..., 2]
    f["colorfulness"] = _round(
        float(np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)), 4)
    f["rg_std"] = _round(float(rg.std()))
    f["yb_std"] = _round(float(yb.std()))

    # 主色（RGB 量化峰）
    q = np.unique((small * 255).astype(np.uint8).reshape(-1, 3), axis=0, return_counts=True)
    order = np.argsort(q[1])[::-1]
    tops = q[0][order[:3]].astype(np.float32) / 255.0
    f["dom_r"] = _round(tops[0][0]); f["dom_g"] = _round(tops[0][1]); f["dom_b"] = _round(tops[0][2])
    f["dom_hsv_h"] = _round(_rgb_to_hsv(tops[0:1])[0, 0])
    f["dom_hsv_s"] = _round(_rgb_to_hsv(tops[0:1])[0, 1])
    f["dom_hsv_v"] = _round(_rgb_to_hsv(tops[0:1])[0, 2])
    # 前 3 主色的真实覆盖率。
    # 踩过的坑：原来用"到各主色的距离 < 0.02"分别计数，容差带互相重叠，
    # 同一个像素被算进多个主色，实测报出 265%、184% 这种超过 100% 的值。
    # 正确做法是按"最近主色"做划分（partition），各主色的像素互斥。
    tot = small.shape[0] * small.shape[1]
    if len(tops):
        d2 = np.stack([((small - tops[i]) ** 2).sum(axis=-1) for i in range(len(tops))], axis=-1)
        nearest = np.argmin(d2, axis=-1)
        nearest_dist = np.sqrt(np.take_along_axis(d2, nearest[..., None], axis=-1)[..., 0])
        counts = np.bincount(nearest.ravel(), minlength=len(tops))
        # 只统计"确实接近主色"的像素，避免把中间过渡色也算进来
        close = nearest_dist < 0.10
        cov = 0.0
        self_cov = []
        for i in range(len(tops)):
            c = float(((nearest == i) & close).sum()) / tot
            self_cov.append(round(c, 4))
            cov += c
        f["dom3_cov"] = _round(min(1.0, cov), 4)
        for i, c in enumerate(self_cov[:3]):
            f[f"dom_cov{i}"] = c
    else:
        f["dom3_cov"] = 0.0
        for i in range(3):
            f[f"dom_cov{i}"] = 0.0

    # 色相直方图（用于看整体色调家族）
    hh = np.histogram(hsv[..., 0][S > 0.12], bins=8, range=(0, 1))[0].astype(np.float64)
    hh = hh / max(1.0, hh.sum())
    for i in range(8):
        f[f"hue_h{i}"] = _round(hh[i], 4)

    # 颜色熵（3D 量化直方图，衡量画面配色复杂度）
    bins = (small * 7.999).astype(np.uint8)
    idx = bins[..., 0].astype(np.int64) * 64 + bins[..., 1].astype(np.int64) * 8 + bins[..., 2].astype(np.int64)
    cnt = np.bincount(idx.ravel(), minlength=512).astype(np.float64)
    pr = cnt / max(1.0, cnt.sum())
    pr = pr[pr > 0]
    f["color_entropy"] = _round(float(-(pr * np.log2(pr)).sum() / 9.0), 4)
    f["unique_colors8"] = int((cnt > 0).sum())

    # ---------- C. 背景与主体 ----------
    # 1) 白底占比
    r, g, b = small[..., 0], small[..., 1], small[..., 2]
    mn = small.min(axis=-1); mx = small.max(axis=-1)
    white = (mn >= 0.94) & ((mx - mn) <= 0.035)
    near_white = (mn >= 0.88) & ((mx - mn) <= 0.06)
    f["white_ratio"] = _round(float(white.mean()))
    f["nearwhite_ratio"] = _round(float(near_white.mean()))

    # 2) 四角与边缘背景块（判断背景是否统一、是否灰底/彩底）
    k = 28
    hh_, ww_ = small.shape[0], small.shape[1]
    kk = max(6, min(k, hh_ // 4, ww_ // 4))
    corners = np.concatenate([
        small[:kk, :kk].reshape(-1, 3), small[:kk, -kk:].reshape(-1, 3),
        small[-kk:, :kk].reshape(-1, 3), small[-kk:, -kk:].reshape(-1, 3)])
    f["corner_mean_r"] = _round(corners[:, 0].mean()); f["corner_mean_g"] = _round(corners[:, 1].mean())
    f["corner_mean_b"] = _round(corners[:, 2].mean())
    f["corner_std"] = _round(float(corners.std(axis=0).mean()))
    f["corner_bright"] = _round(float(corners.mean()))
    f["corner_sat"] = _round(float(_rgb_to_hsv(corners.reshape(1, -1, 3))[0, :, 1].mean()))
    # 四角之间差异（背景是否一致）
    cm = [small[:kk, :kk].mean(axis=(0, 1)), small[:kk, -kk:].mean(axis=(0, 1)),
          small[-kk:, :kk].mean(axis=(0, 1)), small[-kk:, -kk:].mean(axis=(0, 1))]
    cm = np.stack(cm)
    f["corner_consistency"] = _round(float(1.0 - np.clip(cm.std(axis=0).mean() / 0.18, 0, 1)))

    border = np.concatenate([small[:2].reshape(-1, 3), small[-2:].reshape(-1, 3),
                             small[:, :2].reshape(-1, 3), small[:, -2:].reshape(-1, 3)])
    f["border_bright"] = _round(float(border.mean()))
    f["border_sat"] = _round(float(_rgb_to_hsv(border.reshape(1, -1, 3))[0, :, 1].mean()))

    # 3) 背景纯度：border 像素与背景主色的偏离
    bq = (border * 31).astype(np.int32)
    bidx = bq[:, 0] * 32 * 32 + bq[:, 1] * 32 + bq[:, 2]
    bc = np.bincount(bidx, minlength=32 ** 3).astype(np.float64)
    bg_idx = int(np.argmax(bc))
    bg_color = np.array([(bg_idx // 1024) % 32, (bg_idx // 32) % 32, bg_idx % 32], dtype=np.float32) / 31.0
    dist_bg = np.sqrt(((small - bg_color) ** 2).sum(axis=-1))
    f["bg_purity"] = _round(float((dist_bg < 0.08).mean()))
    f["bg_dark"] = _round(float(bg_color.mean() < 0.35))
    f["bg_is_colored"] = _round(float(_rgb_to_hsv(bg_color.reshape(1, 1, 3))[0, 0, 1] > 0.12))
    f["bg_color_r"] = _round(bg_color[0]); f["bg_color_g"] = _round(bg_color[1]); f["bg_color_b"] = _round(bg_color[2])

    # 4) 背景连通性 —— 白底图的关键指标：
    #    背景应该是一整片连通的区域，且没有大片孤立白块（那通常是补丁/修图痕迹）
    bgmask = (dist_bg < 0.10)
    lab_bg, n_bg = _label(bgmask)
    if n_bg:
        sizes = np.bincount(lab_bg.ravel())
        sizes[0] = 0
        big = int(sizes.argmax())
        f["bg_largest_frac"] = _round(float(sizes[big]) / bgmask.size)
        f["bg_isolated_white"] = _round(float((sizes.sum() - sizes[big]) / bgmask.size))
        f["bg_components"] = int(n_bg)
    else:
        f["bg_largest_frac"] = 0.0
        f["bg_isolated_white"] = 0.0
        f["bg_components"] = 0
    # 只在"白底/纯色底"前提下，孤立色块才说明抠图残留。
    # 实测教训：生活场景图里本来就有成片白色（床单、墙面），不加门控会把
    # 正常场景图误判成"修图痕迹明显"。
    white_bg_mode = bool(f["white_ratio"] >= 0.5 or f["bg_purity"] >= 0.6)
    f["bg_white_mode"] = white_bg_mode
    f["bg_isolated_white_gated"] = _round(
        f["bg_isolated_white"] if white_bg_mode else 0.0)
    if not white_bg_mode:
        f["bg_isolated_white"] = 0.0

    # 5) 主体显著性：边缘色 vs 各像素
    subj = dist_bg > 0.18
    area = float(subj.mean())
    if area < 0.03 or area > 0.985:      # 背景法失效 -> 中心-周边法
        if area > 0.985:
            cy, cx = hh_ // 2, ww_ // 2
            yy, xx = np.mgrid[0:hh_, 0:ww_]
            rad = np.sqrt(((yy - cy) / hh_) ** 2 + ((xx - cx) / ww_) ** 2)
            wgt = np.exp(-(rad ** 2) / (2 * 0.30 ** 2))
            mu = (small * wgt[..., None]).sum(axis=(0, 1)) / wgt.sum()
            subj = np.sqrt(((small - mu) ** 2).sum(axis=-1)) > 0.22
        else:
            c = small[hh_ // 4:3 * hh_ // 4, ww_ // 4:3 * ww_ // 4].reshape(-1, 3)
            cq = (c * 15).astype(np.int32)
            cc = np.bincount(cq[:, 0] * 256 + cq[:, 1] * 16 + cq[:, 2], minlength=4096)
            ci = int(np.argmax(cc))
            ccol = np.array([(ci // 256) % 16, (ci // 16) % 16, ci % 16], dtype=np.float32) / 15.0
            subj = np.sqrt(((small - ccol) ** 2).sum(axis=-1)) > 0.16

    # 去噪：开运算
    subj = _erode(_dilate(subj, 1), 1)
    lab_s, n_s = _label(subj)
    subj_mask = np.zeros_like(subj)
    if n_s:
        _sz = np.bincount(lab_s.ravel()); _sz[0] = 0
        subj_mask = (lab_s == int(_sz.argmax()))

    # 5b) 过曝只在主体上测 —— 白底图的背景本来就是白的，拿全图算会冤枉它
    #     （实测：干净白底图曾被判"67% 死白"，纯属误伤）
    if subj_mask.any():
        f["clip_white_subject"] = _round(float((gray_s[subj_mask] > 0.98).mean()))
        f["clip_black_subject"] = _round(float((gray_s[subj_mask] < 0.02).mean()))
        f["highlight_flat_subject"] = _round(float((gray_s[subj_mask] > 0.985).mean()))
        f["subject_contrast"] = _round(float(gray_s[subj_mask].std()))
    else:
        f["clip_white_subject"] = 0.0
        f["clip_black_subject"] = 0.0
        f["highlight_flat_subject"] = 0.0
        f["subject_contrast"] = 0.0

    if n_s:
        sizes = np.bincount(lab_s.ravel()); sizes[0] = 0
        big = int(sizes.argmax())
        m = subj_mask
        ys, xs = np.nonzero(m)
        y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
        f["subject_area"] = _round(float(m.mean()))
        f["subject_bbox_fill"] = _round(float(m.sum()) / max(1.0, (y1 - y0 + 1) * (x1 - x0 + 1)))
        f["subject_bbox_w"] = _round((x1 - x0 + 1) / ww_)
        f["subject_bbox_h"] = _round((y1 - y0 + 1) / hh_)
        f["subject_bbox_max"] = _round(max(x1 - x0 + 1, y1 - y0 + 1) / max(ww_, hh_))
        f["subject_centroid_dx"] = _round((xs.mean() - ww_ / 2) / ww_)
        f["subject_centroid_dy"] = _round((ys.mean() - hh_ / 2) / hh_)
        f["subject_centered"] = _round(1.0 - min(1.0, math.hypot(
            (xs.mean() - ww_ / 2) / (ww_ / 2), (ys.mean() - hh_ / 2) / (hh_ / 2))))
        f["subject_touch_edges"] = int((y0 <= 1) + (x0 <= 1) + (y1 >= hh_ - 2) + (x1 >= ww_ - 2))
        f["subject_margin_top"] = _round(y0 / hh_); f["subject_margin_bottom"] = _round((hh_ - 1 - y1) / hh_)
        f["subject_margin_left"] = _round(x0 / ww_); f["subject_margin_right"] = _round((ww_ - 1 - x1) / ww_)
        f["subject_aspect"] = _round((x1 - x0 + 1) / max(1.0, (y1 - y0 + 1)))
        f["subject_sat"] = _round(float(S[m].mean()))
        f["subject_bright"] = _round(float(gray_s[m].mean()))
        f["subject_std"] = _round(float(gray_s[m].std()))
        # 主体颜色与背景色差（主体是否从背景里"跳"出来）
        f["subject_bg_dist"] = _round(float(dist_bg[m].mean()))
        f["subject_n_components"] = int(n_s)
    else:
        for kk_ in ("subject_area", "subject_bbox_fill", "subject_bbox_w", "subject_bbox_h",
                    "subject_bbox_max", "subject_centroid_dx", "subject_centroid_dy",
                    "subject_centered", "subject_margin_top", "subject_margin_bottom",
                    "subject_margin_left", "subject_margin_right", "subject_aspect",
                    "subject_sat", "subject_bright", "subject_std", "subject_bg_dist"):
            f[kk_] = 0.0
        f["subject_touch_edges"] = 0
        f["subject_n_components"] = 0

    # 6) 肤色（判断是否有人/手出镜）
    Yc = 0.299 * r + 0.587 * g + 0.114 * b
    Cb = 128 + (-0.168736 * r - 0.331264 * g + 0.5 * b) * 255
    Cr = 128 + (0.5 * r - 0.418688 * g - 0.081312 * b) * 255
    # 收紧门槛 + 加色相/饱和守卫：原来的宽松阈值会把米灰色布料整片误判成皮肤
    # （实测：灰褐色毯子在旧阈值下报出 37% "肤色"）
    hue = hsv[..., 0]
    skin = ((Yc * 255 > 80) & (Cb > 85) & (Cb < 130) & (Cr > 138) & (Cr < 176) &
            (hue < 0.10) & (S > 0.10) & (S < 0.65))
    f["skin_raw"] = _round(float(skin.mean()))
    if skin.any():
        labk, nk = _label(skin)
        sizes = np.bincount(labk.ravel()); sizes[0] = 0
        biggest = float(sizes.max()) if nk else 0.0
        f["skin_max_blob"] = _round(biggest / skin.size)
        area = biggest / skin.size
        # 只认"成片"的皮肤：零散像素不算人
        if biggest >= 0.002 * skin.size:
            # 再加两道守卫，专治"平涂的米色/暖灰底色被当成皮肤"：
            #   实测教训：万圣节印花图案的米色底报出 51.6% 肤色，直接把图案图
            #   误判成"模特展示图"。
            #   守卫1 —— 占比上限：真人脸+手在商品图里不会占到画面三成以上；
            #   守卫2 —— 纹理要求：皮肤有明暗过渡，平涂色块几乎零方差。
            lab_sel = (labk == int(sizes.argmax()))
            texture = float(gray_s[lab_sel].std())
            f["skin_texture"] = _round(texture)
            f["skin_area"] = _round(area)
            f["skin_ratio"] = _round(area if (area <= 0.30 and texture >= 0.035) else 0.0)
            f["skin_rejected_flat"] = bool(area > 0.30 or texture < 0.035)
        else:
            f["skin_ratio"] = 0.0
            f["skin_texture"] = 0.0
            f["skin_area"] = _round(area)
            f["skin_rejected_flat"] = False
    else:
        f["skin_max_blob"] = 0.0
        f["skin_ratio"] = 0.0
        f["skin_texture"] = 0.0
        f["skin_area"] = 0.0
        f["skin_rejected_flat"] = False

    # ---------- D. 画面干扰：文字 / 水印 ----------
    # 边缘密度
    gx = np.zeros_like(gray_s); gy = np.zeros_like(gray_s)
    gx[:, 1:-1] = gray_s[:, 2:] - gray_s[:, :-2]
    gy[1:-1, :] = gray_s[2:, :] - gray_s[:-2, :]
    mag = np.sqrt(gx * gx + gy * gy)
    f["edge_density"] = _round(float((mag > 0.08).mean()))
    f["edge_density_strong"] = _round(float((mag > 0.20).mean()))
    f["hf_energy"] = _round(float(mag.mean()), 5)

    # 文字/水印检测：双通道打分
    #   通道1 亮度笔画：亮底暗字 / 暗底亮字，像素级，擅长小字
    #   通道2 颜色显著性：与主导背景色明显不同的成片像素，擅长彩色大字水印
    #      （实测发现：白色底上的红色 "LIMITED OFFER 50% OFF" 用通道1 完全漏检）
    def _blob_stats(mask_in: np.ndarray) -> tuple[int, float, float]:
        """返回 (字符状块数, 覆盖占比, 边缘偏向)。"""
        m = _erode(_dilate(mask_in, 1), 1)
        lb, n = _label(m)
        if not n:
            return 0, 0.0, 0.0
        sizes = np.bincount(lb.ravel())
        idxs = np.nonzero(sizes)[0]
        idxs = idxs[idxs != 0]
        good = 0
        near_edge = 0
        for i in idxs:
            s = int(sizes[i])
            if s < 6 or s > max(40, int(0.05 * m.size)):
                continue
            ys, xs = np.nonzero(lb == i)
            bw, bh = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
            ar = bw / max(1, bh)
            if not (0.06 <= ar <= 16 and 3 <= bw and 3 <= bh):
                continue
            fill = s / float(bw * bh)
            if fill < 0.16:
                continue
            peri = 2.0 * (bw + bh)
            stroke = 2.0 * s / max(1.0, peri)
            if stroke < 0.8 or stroke > max(6.0, 0.06 * max(bw, bh)):
                continue
            good += 1
            if (ys.min() <= 0.18 * hh_ or ys.max() >= 0.82 * hh_ or
                    xs.min() <= 0.12 * ww_ or xs.max() >= 0.88 * ww_):
                near_edge += 1
        frac = float(m.mean())
        return good, frac, (near_edge / good if good else 0.0)

    # 通道1：亮度笔画。阈值随背景明暗自动翻转，避免"亮底上找亮块"
    n_blob_code = 0
    frac_code = 0.0
    edge_code = 0.0
    for pol in ((gray_s > 0.55) & (mag > 0.14), (gray_s < 0.45) & (mag > 0.14)):
        _g, _fr, _ed = _blob_stats(pol)
        n_blob_code += _g
        frac_code += _fr
        edge_code += _ed

    # 通道2：颜色显著性（针对"白色/浅色底 + 彩色文字"这种最典型的促销水印）
    n_blob_col = 0
    frac_col = 0.0
    edge_col = 0.0
    if bg_color.mean() > 0.55 and gray_s.std() > 0.02:
        far = dist_bg > 0.34
        near_white_bg = dist_bg < 0.16
        cand = far & (~near_white_bg)
        _g, _fr, _ed = _blob_stats(cand)
        # 颜色块也可能是产品本身的花色，所以要求"多块 + 集中在边缘"
        if _g >= 6 and _ed > 0.35:
            n_blob_col, frac_col, edge_col = _g, _fr, _ed

    f["text_blobs"] = int(n_blob_code)
    f["text_blob_frac"] = _round(frac_code)
    f["text_edge_bias"] = _round(edge_code / 2.0)
    f["text_blobs_color"] = int(n_blob_col)
    f["text_color_frac"] = _round(frac_col)
    f["text_color_edge_bias"] = _round(edge_col, 3)

    # 通道3：贴边集中度。促销大字/水印几乎都压在画面边缘或角上，而产品本身的花色与绒面
    #        是铺满画面的渐变。用"边框区边缘能量 / 中心区边缘能量"的比值来区分。
    #        教训：一开始想用"与背景色的色差"找文字，实测失败 —— 浅灰底上的红字色差只有
    #        0.28，够不到阈值；换成边缘能量后信号清晰。
    band_y, band_x = max(2, int(0.15 * hh_)), max(2, int(0.15 * ww_))
    frame = np.ones_like(mag, dtype=bool)
    frame[band_y:hh_ - band_y, band_x:ww_ - band_x] = False
    inner = ~frame
    e_frame = float(mag[frame].mean()) if frame.any() else 0.0
    e_inner = float(mag[inner].mean()) if inner.any() else 0.0
    f["edge_frame_mean"] = _round(e_frame, 5)
    f["edge_inner_mean"] = _round(e_inner, 5)
    f["overlay_concentration"] = _round(e_frame / max(0.010, e_inner), 3)

    # 通道3：黑帽形态学笔画检测（对"文字"最本质的判据 —— 比背景暗的细笔画）
    #   文字 = 局部比周围暗、笔画宽度窄且一致的元素，跟它摆在哪个位置无关。
    #   实测：靠"贴边集中度"判断会漏掉摆在画面中上部的促销大字，黑帽法的区分度
    #   是干净图 0.031 对带字图 0.175（5.6 倍），且对半透明水印同样灵敏。
    bh_stroke_frac = 0.0
    bh_blobs = 0
    bh_peak = 0.0
    bh_bbox = 0.0
    bh_bands = 0
    try:
        g8 = Image.fromarray((np.clip(gray_s, 0, 1) * 255).astype(np.uint8), "L")
        op = g8.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
        op_arr = np.asarray(op, dtype=np.float32) / 255.0
        bh = op_arr - np.asarray(g8, dtype=np.float32) / 255.0
        bm = bh > 0.10
        bh_stroke_frac = float(bm.mean())
        bh_peak = float(bh.max())
        if bm.any():
            ys, xs = np.nonzero(bm)
            bh_bbox = float((ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)) / float(bm.size)
            rowsum = bm.sum(axis=1).astype(np.float32)
            if rowsum.max() > 0:
                thr = 0.10 * rowsum.max()
                above = rowsum > thr
                bh_bands = int(np.count_nonzero(above[1:] & ~above[:-1])) + int(above[0])
            bw = max(6, min(15, hh_ // 30))
            bm2 = _erode(_dilate(bm, 1), 1)
            lb, n = _label(bm2)
            if n:
                sizes = np.bincount(lb.ravel())
                idxs = np.nonzero(sizes)[0]
                idxs = idxs[idxs != 0]
                for i in idxs:
                    s = int(sizes[i])
                    if s < 5:
                        continue
                    yy, xx = np.nonzero(lb == i)
                    hb = int(yy.max() - yy.min() + 1)
                    wb = int(xx.max() - xx.min() + 1)
                    if 2 <= wb and 3 <= hb <= max(30, bw * 4) and (wb / max(1, hb)) <= 20:
                        bh_blobs += 1
    except Exception:
        pass
    # 精细化：叠加文字的关键特征是"近纯黑或近纯白 + 与本地背景高对比"，
    # 而针织纹理/报纸印刷是中灰调、对比度参差。
    # 实测教训：只看"有没有笔画"，会把麻花针织毯整片判成文字（24 张真实网图里误报 18 张）。
    bh_dark = 0.0
    bh_light = 0.0
    bh_dark_blobs = 0
    bh_light_blobs = 0
    try:
        if bh.size and bh_stroke_frac > 0.0:
            bm_raw = bh > 0.10
            gv = gray_s
            dark_side = bm_raw & (gv < 0.42) & (gv < 0.72 * op_arr)
            light_side = bm_raw & (gv > 0.62) & (gv > 1.28 * op_arr)
            bh_dark = float(dark_side.mean())
            bh_light = float(light_side.mean())
            for mm, cnt_name in ((dark_side, "dark"), (light_side, "light")):
                if mm.sum() < 8:
                    continue
                m2 = _erode(_dilate(mm, 1), 1)
                lb, n = _label(m2)
                if not n:
                    continue
                sizes = np.bincount(lb.ravel())
                idxs = np.nonzero(sizes)[0]
                idxs = idxs[idxs != 0]
                good = 0
                for i in idxs:
                    s = int(sizes[i])
                    if s < 6:
                        continue
                    yy, xx = np.nonzero(lb == i)
                    hb = int(yy.max() - yy.min() + 1)
                    wb = int(xx.max() - xx.min() + 1)
                    if hb < 3 or wb < 2:
                        continue
                    if (wb / max(1, hb)) > 25:
                        continue
                    if s / float(wb * hb) < 0.15:
                        continue
                    good += 1
                if cnt_name == "dark":
                    bh_dark_blobs = good
                else:
                    bh_light_blobs = good
    except Exception:
        pass
    f["bh_dark_frac"] = _round(bh_dark)
    f["bh_light_frac"] = _round(bh_light)
    f["bh_dark_blobs"] = int(bh_dark_blobs)
    f["bh_light_blobs"] = int(bh_light_blobs)
    f["bh_stroke_frac"] = _round(bh_stroke_frac)
    f["bh_blobs"] = int(bh_blobs)
    f["bh_peak"] = _round(bh_peak, 4)
    f["bh_bbox_frac"] = _round(bh_bbox, 4)
    f["bh_bands"] = int(bh_bands)

    # 分块边缘密度：找最"吵"的一块
    bsz = 32
    ny, nx = hh_ // bsz, ww_ // bsz
    peak = 0.0
    peak_rows = set()
    if ny >= 2 and nx >= 2:
        blocks = mag[:ny * bsz, :nx * bsz].reshape(ny, bsz, nx, bsz).mean(axis=(1, 3))
        flat = blocks.ravel()
        peak = float(flat.max())
        thr2 = max(0.02, float(np.median(flat)) * 3.0)
        hot = np.argwhere(blocks > thr2)
        peak_rows = {int(rv) for rv, _ in hot}
    f["edge_block_peak"] = _round(peak, 5)
    f["edge_hot_rows"] = len(peak_rows)

    # 综合文字/水印嫌疑：四通道取最强信号
    score_code = min(1.0, n_blob_code / 180.0)
    score_col = min(1.0, n_blob_col / 60.0) * (1.0 if n_blob_col >= 6 else 0.0)
    score_conc = 0.0
    if e_inner > 0.005 and f["overlay_concentration"] > 2.0 and e_frame > 0.012:
        score_conc = min(1.0, (f["overlay_concentration"] - 2.0) / 3.0)
    # 黑帽通道打分：只看"笔画质量"，不看"摆在哪"。
    #   主信号是笔画占比；对比度和密集度只做温和加权，且各有下限。
    #   教训：一开始用三个乘数连乘，结果水印被打成 0.04（本该 0.9）——
    #   乘数越多，信号被削得越狠。
    mass = max(0.0, bh_stroke_frac - 0.0015)
    clarity = min(1.0, bh_peak / 0.35) if bh_peak > 0.12 else 0.0
    density = min(1.0, bh_blobs / 40.0)
    compact = 1.0 if 0.0 < bh_bbox < 0.55 else 0.85
    score_bh = min(1.0, 3.0 * mass * (0.75 + 0.25 * clarity) * (0.65 + 0.35 * density) * compact)
    if bh_peak > 0.55 and bh_stroke_frac > 0.0012:      # 极小但极清晰的字
        score_bh = max(score_bh, 0.55)
    f["text_score"] = _round(max(score_code, score_col, score_conc, score_bh), 4)
    f["text_channels"] = {"stroke_blobs": _round(score_code, 4), "color_blobs": _round(score_col, 4),
                          "edge_conc": _round(score_conc, 4), "blackhat": _round(score_bh, 4)}
    f["has_text_suspect"] = bool(f["text_score"] >= 0.50 or
                                 (n_blob_code >= 60 and frac_code > 0.012) or
                                 (n_blob_col >= 6 and frac_col > 0.0015 and edge_col > 0.35) or
                                 (e_inner > 0.005 and e_frame > 0.020 and
                                  f["overlay_concentration"] > 3.5))
    # 中等嫌疑：有笔画信号但没到阈值，提示人工确认（细织唛文字、淡角标、半透明水印）
    f["text_warn"] = bool(not f["has_text_suspect"] and
                          (f["text_score"] >= 0.14 or bh_blobs >= 8 or n_blob_code >= 25))
    # 中等嫌疑：有笔画信号但没到阈值，提示人工确认（细织唛文字、淡角标、半透明水印）
    f["text_warn"] = bool(not f["has_text_suspect"] and
                          (f["text_score"] >= 0.20 or bh_blobs >= 8 or
                           bh_peak > 0.55 or n_blob_code >= 25))

    # 网格指纹（风格相似度 / 查重）
    for gy_ in range(GRID):
        for gx_ in range(GRID):
            blk = small[gy_ * hh_ // GRID:(gy_ + 1) * hh_ // GRID,
                        gx_ * ww_ // GRID:(gx_ + 1) * ww_ // GRID]
            f[f"fp{gy_}{gx_}"] = _round(float(blk.mean()), 4)
    # 差异哈希（感知查重）
    d8 = np.asarray(Image.fromarray((gray_s * 255).astype(np.uint8), "L").resize((9, 8), Image.BILINEAR),
                    dtype=np.float32)
    bits = (d8[:, 1:] > d8[:, :-1]).ravel()
    f["phash"] = f"{int(''.join('1' if x else '0' for x in bits), 2):016x}"
    f["bright_grid_std"] = _round(float(np.std([f[f"fp{i}{j}"] for i in range(GRID) for j in range(GRID)])), 4)

    return f


# 特征列顺序（供表格 / 模型使用）
def feature_columns(rows: list[dict]) -> list[str]:
    skip = {"file", "name", "ok", "error", "md5_16", "phash", "orig_format", "orig_mode",
            "fmt_version", "mtime", "bytes"}
    cols: list[str] = []
    for r in rows:
        for k, v in r.items():
            if k in skip or k in cols:
                continue
            if isinstance(v, bool) or isinstance(v, (int, float)):
                cols.append(k)
    return cols
