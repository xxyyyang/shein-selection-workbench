# -*- coding: utf-8 -*-
"""
一键清理废图：把所有批次过一遍，列出/移走废图。

用法：
  python -m shein_scout junk --scan          # 只扫描，出报告
  python -m shein_scout junk --clean         # 扫描并移到回收站
  python -m shein_scout junk --clean --only wm,dup   # 只清水印和重复
"""
from __future__ import annotations

import functools
import json
import sys
import time
from pathlib import Path

# 让输出实时刷出来（GUI 里 PIPE 会缓冲，用户会以为卡死）
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from . import junk as junk_mod


# 这些目录不算待清理的图
SKIP_DIRS = {"_缩略图", "__pycache__", "_缓存", "回收站"}


def collect_images(root: Path, archive: Path = None) -> dict:
    """收集待检查的图。

    只从**一个**地方收集（优先存档目录），否则硬链接会导致同一张图被算两次。
    """
    roots = []
    if archive and Path(archive).exists():
        roots.append(Path(archive))
    else:
        roots.append(Path(root) / "patterns")
    out = {}
    for r in roots:
        for f in r.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            out[str(f)] = f
    return out


def cmd_junk(args) -> int:
    from . import library as lib

    root = Path(getattr(args, "root", None) or ".")
    archive = getattr(args, "archive", "") or ""
    do_clean = getattr(args, "clean", False)
    only = [x.strip() for x in (getattr(args, "only", "") or "").split(",") if x.strip()]
    limit = getattr(args, "limit", 0) or 0
    do_wm = (not only) or ("wm" in only)
    do_dup = (not only) or ("dup" in only)
    do_small = (not only) or ("small" in only)

    imgs = collect_images(root, archive)
    paths = list(imgs.values())
    print("=" * 78)
    print("废图扫描")
    print("=" * 78)
    print(f"  找到 {len(paths)} 张图")
    print(f"  检查项: {'水印 ' if do_wm else ''}{'重复 ' if do_dup else ''}"
          f"{'太小' if do_small else ''}")

    bad: dict[str, list] = {"水印": [], "重复": [], "太小": []}

    # ---- 水印 ----
    if do_wm:
        print("\n  ▸ 查水印（慢，要 OCR 每张图）...")
        wm = junk_mod.scan_watermarks(paths, limit=limit)
        for k, ev in wm.items():
            bad["水印"].append((Path(k), ev))
        print(f"    检出 {len(wm)} 张带水印")

    # ---- 重复 ----
    if do_dup:
        print("\n  ▸ 查重复 ...")
        dups = junk_mod.find_duplicates(paths)
        n_extra = 0
        for sha, group in dups.items():
            # 保留第一个，其余算废
            keep = sorted(group, key=lambda p: -p.stat().st_size)[0]
            for p in group:
                if p != keep:
                    bad["重复"].append((p, f"与 {keep.name} 相同"))
                    n_extra += 1
        print(f"    检出 {len(dups)} 组重复，多出 {n_extra} 张")

    # ---- 太小 ----
    if do_small:
        print("\n  ▸ 查尺寸 ...")
        from PIL import Image
        for p in paths:
            try:
                with Image.open(p) as im:
                    w, h = im.size
                if min(w, h) < 600:
                    bad["太小"].append((p, f"{w}x{h}"))
            except Exception:
                bad["太小"].append((p, "打不开"))
        print(f"    检出 {len(bad['太小'])} 张太小/打不开")

    # ---- 报告 ----
    total = sum(len(v) for v in bad.values())
    print()
    print("=" * 78)
    print("报告")
    print("=" * 78)
    for k, v in bad.items():
        print(f"  {k:6s}: {len(v):4d} 张")
    print(f"  合计  : {total:4d} 张（占 {total/max(len(paths),1):.0%}）")

    if not do_clean:
        print()
        print("  （这是扫描，没有动文件。加 --clean 才会移到回收站）")
        # 写出清单
        rep = Path(root) / "work" / "废图清单.json"
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text(json.dumps(
            {k: [[str(p), ev] for p, ev in v] for k, v in bad.items()},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  清单已写: {rep}")
        return 0

    # ---- 清理 ----
    rec = Path(root).parent / "回收站"
    if archive:
        rec = Path(archive).parent / "回收站"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = rec / f"废图_{stamp}"
    dst.mkdir(parents=True, exist_ok=True)
    import shutil
    moved = 0
    for k, v in bad.items():
        for p, ev in v:
            try:
                tgt = dst / p.name
                i = 1
                while tgt.exists():
                    tgt = dst / f"{p.stem}_{i}{p.suffix}"
                    i += 1
                shutil.move(str(p), str(tgt))
                moved += 1
            except Exception as e:
                print(f"    移动失败 {p.name}: {e}")
    print(f"\n  已移走 {moved} 张 -> {dst}")
    print(f"  恢复：把文件从那里搬回去即可")
    return 0
