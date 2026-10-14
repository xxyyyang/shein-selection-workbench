# -*- coding: utf-8 -*-
"""
shein_scout.io_utils —— 找图、缓存、读写

要点：
* find_images: 递归扫描目录，按扩展名 + 真实解码试探双重确认（不靠后缀猜）。
* FeatureCache: 以 (绝对路径, 大小, mtime, 特征版本) 为键缓存特征 JSON，
  重复跑同一批图时几乎瞬时，方便你反复调参。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import features

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
           ".avif", ".heic", ".jfif", ".ppm"}
SKIP_DIRS = {"$recycle.bin", "system volume information", "node_modules", ".git",
             "__pycache__", ".cache", "appdata"}


def find_images(paths, recursive: bool = True, limit: int | None = None) -> list[Path]:
    """把一个或多个 目录/文件 展开成图片文件清单（去重、按路径排序）。"""
    if isinstance(paths, (str, Path)):
        paths = [paths]
    out: list[Path] = []
    seen: set[str] = set()

    def push(p: Path) -> None:
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)

    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_file():
            push(p)
            continue
        if not p.is_dir():
            continue
        if recursive:
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs
                           if d.lower() not in SKIP_DIRS and not d.startswith(".")]
                for fn in files:
                    if Path(fn).suffix.lower() in IMG_EXT:
                        push(Path(root) / fn)
        else:
            for fn in sorted(os.listdir(p)):
                fp = p / fn
                if fp.is_file() and fp.suffix.lower() in IMG_EXT:
                    push(fp)
    out.sort(key=lambda x: str(x).lower())
    if limit is not None:
        out = out[:limit]
    return out


class FeatureCache:
    """特征磁盘缓存。放在 work/cache/features.json，键为 路径|大小|mtime|版本。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        self.hits = 0
        self.misses = 0
        self._dirty = False
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    obj = json.load(fh)
                if isinstance(obj, dict) and obj.get("_version") == features.FMT_VERSION:
                    self.data = obj.get("items", {})
            except Exception:
                self.data = {}

    @staticmethod
    def key_for(p: Path) -> str:
        try:
            st = p.stat()
            return f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}|v{features.FMT_VERSION}"
        except Exception:
            return f"{p}|0|0|v{features.FMT_VERSION}"

    def get(self, p: Path) -> dict | None:
        k = self.key_for(p)
        v = self.data.get(k)
        if v is not None:
            self.hits += 1
        else:
            self.misses += 1
        return v

    def put(self, p: Path, feat: dict) -> None:
        self.data[self.key_for(p)] = feat
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"_version": features.FMT_VERSION, "items": self.data},
                      fh, ensure_ascii=False)
        tmp.replace(self.path)


def write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_csv(path: str | Path, rows: list[dict], columns: list[str] | None = None) -> None:
    """自己写 CSV，避免额外依赖；用 utf-8-sig 让 Excel 打开不乱码。"""
    import csv
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        p.write_text("", encoding="utf-8-sig")
        return
    if columns is None:
        columns = list(rows[0].keys())
        for r in rows[1:]:
            for k in r:
                if k not in columns:
                    columns.append(k)
    with open(p, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"
