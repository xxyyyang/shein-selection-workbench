# -*- coding: utf-8 -*-
"""
shein_scout/junk.py —— 自动识别废图

三类客观废图（不需要人看就能判）：
  1. 带水印/版权（Shutterstock/图虫/Alamy 等素材站样图）-> 用了侵权
  2. 内容重复（同一张图多次入库）
  3. 太小/太糊（做不了印花）

为什么重要：实测抽样 60 张里有 21 张（35%）带水印。
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

# 素材站特征词（OCR 文本里出现就判定）
WATERMARK_KEYS = (
    "shutterstock", "tuchong", "alamy", "gettyimages", "dreamstime",
    "123rf", "vecteezy", "freepik", "depositphotos", "istockphoto",
    "istock", "hellorf", "zcool", "adobestock", "pond5", "envato",
    "图虫", "视觉中国", "千图", "千库", "包图", "昵图", "摄图", "汇图",
    "veer", "pic123", "58pic", "ooopic",
)
COPYRIGHT_KEYS = ("copyright", "allrightsreserved", "llc", "ltd", "inc")
ID_KEYS = ("imageid", "素材编号", "编号")


def norm(t: str) -> str:
    """OCR 文本归一化：只留字母数字和中文，去噪。"""
    return "".join(c for c in (t or "").lower()
                   if c.isalnum() or "\u4e00" <= c <= "\u9fff")


def check_watermark(path: Path) -> tuple[bool, str]:
    """返回 (是否带水印, 证据)。"""
    try:
        from . import ocr
        r = ocr.read_text(path)
        txt = r if isinstance(r, str) else (r or {}).get("text", "")
    except Exception:
        return False, ""
    n = norm(txt)
    for k in WATERMARK_KEYS:
        if k in n:
            return True, f"素材站: {k}"
    hits = [k for k in COPYRIGHT_KEYS if k in n]
    if len(hits) >= 1 and any(x in n for x in ("imageid", "id", "www")):
        return True, f"版权标记: {','.join(hits)}"
    if "www" in n and len(n) > 12 and any(
            x in n for x in ("com", "cn", "net")):
        return True, "含网址水印"
    return False, ""


def scan_watermarks(paths: list[Path], limit: int = 0,
                    verbose: bool = True) -> dict:
    """批量查水印。返回 {path: 证据}。"""
    todo = paths[:limit] if limit else paths
    out = {}
    for i, p in enumerate(todo, 1):
        wm, ev = check_watermark(p)
        if wm:
            out[str(p)] = ev
        if verbose and i % 25 == 0:
            print(f"      水印检测 [{i}/{len(todo)}] 已检出 {len(out)} 张")
    return out


def find_duplicates(paths: list[Path]) -> dict:
    """找内容完全相同的图。返回 {sha1: [paths]}（只含重复的）。"""
    by = defaultdict(list)
    for p in paths:
        try:
            by[hashlib.sha1(p.read_bytes()).hexdigest()].append(p)
        except Exception:
            pass
    return {k: v for k, v in by.items() if len(v) > 1}


# ---------------------------------------------------------------- 感知哈希
def dhash(path: Path, size: int = 8) -> int:
    """算图的感知哈希（dHash）。返回 64 位整数。

    做法：缩成 (size+1) x size 灰度图，比较每行相邻像素的明暗，
    得到 size*size 位指纹。对缩放/轻微裁切/压缩都稳定。
    """
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("L").resize((size + 1, size), Image.LANCZOS)
            px = list(im.getdata())
        bits = 0
        for row in range(size):
            for col in range(size):
                left = px[row * (size + 1) + col]
                right = px[row * (size + 1) + col + 1]
                bits = (bits << 1) | (1 if left > right else 0)
        return bits
    except Exception:
        return -1


def hamming(a: int, b: int) -> int:
    """两个哈希的汉明距离（不同位的个数）。"""
    if a < 0 or b < 0:
        return 999
    return bin(a ^ b).count("1")


def find_near_duplicates(paths: list[Path], threshold: int = 6,
                         verbose: bool = True) -> list[list[Path]]:
    """找**内容近似**的图（不只是完全相同）。

    threshold=6 表示 64 位里有 6 位不同就算同一张 —— 这个值偏严，
    不会把"相似风格"误判成重复。
    """
    items = []
    for i, p in enumerate(paths, 1):
        h = dhash(p)
        if h >= 0:
            items.append((h, p))
        if verbose and i % 100 == 0:
            print(f"      感知哈希 [{i}/{len(paths)}]", flush=True)
    groups = []
    used = set()
    for i, (h1, p1) in enumerate(items):
        if i in used:
            continue
        grp = [p1]
        for j in range(i + 1, len(items)):
            if j in used:
                continue
            h2, p2 = items[j]
            if hamming(h1, h2) <= threshold:
                grp.append(p2)
                used.add(j)
        if len(grp) > 1:
            groups.append(grp)
    return groups
