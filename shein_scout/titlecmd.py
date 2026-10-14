# -*- coding: utf-8 -*-
"""
批量生成标题：对图案库里的图逐张调 gen1shot，结果存 JSON。

用法：
    python -m shein_scout titlegen --batch              # 全部没生成的
    python -m shein_scout titlegen --batch --limit 20   # 试 20 张
    python -m shein_scout titlegen --one <图片路径>      # 单张
    python -m shein_scout titlegen --show               # 看已生成的
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CACHE_NAME = "titles.json"


def _paths(root: Path, archive: Path = None) -> list:
    """列出要生成标题的图（用存档目录，路径稳定）。"""
    lib = root / "patterns" / "library.jsonl"
    recs = []
    if lib.exists():
        for line in lib.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    # 解析实际文件路径
    ar = Path(archive) if archive else None
    out = []
    for r in recs:
        fname = (r.get("rel") or r.get("file") or "").replace("\\", "/").split("/")[-1]
        if not fname:
            continue
        key = r["batch"] + "/" + fname
        p = None
        if ar:
            cand = ar / r["batch"] / fname
            if cand.exists():
                p = cand
        if p is None:
            base = root / "patterns" / r["batch"]
            if base.exists():
                c = base / fname
                if c.exists():
                    p = c
                else:
                    for sub in base.iterdir():
                        if sub.is_dir() and (sub / fname).exists():
                            p = sub / fname
                            break
        out.append({"key": key, "batch": r["batch"], "file": fname, "path": p})
    return out


def cmd_titlegen(args) -> int:
    from . import gen1shot

    root = Path(getattr(args, "root", None) or ".")
    archive = getattr(args, "archive", "") or ""
    if not archive:
        try:
            from .paths import ARCHIVE as _A
            archive = str(_A)
        except Exception:
            pass
    cache = root / "work" / CACHE_NAME
    data = {}
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    # 单张
    one = getattr(args, "one", "") or ""
    if one:
        p = Path(one)
        if not p.exists():
            print(f"文件不存在: {p}")
            return 1
        print(f"生成中: {p.name} ...")
        r = gen1shot.gen_one(p, verbose=True)
        if not r.get("ok"):
            print(f"失败: {r.get('error')}")
            return 1
        print(json.dumps({k: v for k, v in r.items() if k != "豆包原始"},
                         ensure_ascii=False, indent=2))
        return 0

    # 只看
    if getattr(args, "show", False):
        if not data:
            print("还没有生成过标题")
            return 0
        print(f"已生成 {len(data)} 条\n")
        for i, (k, v) in enumerate(list(data.items())[:30], 1):
            print(f"{i:3d}. [{v.get('字数', '?'):3}字] {v.get('标题', '')}")
        return 0

    # 批量
    imgs = _paths(root, archive)
    limit = getattr(args, "limit", 0) or 0
    todo = [x for x in imgs if x["path"] and x["key"] not in data]
    if limit:
        todo = todo[:limit]
    print("=" * 78)
    print("批量生成标题")
    print("=" * 78)
    print(f"  库里 {len(imgs)} 张，已生成 {len(data)} 条，本次生成 {len(todo)} 条")
    if not todo:
        print("  没有要生成的")
        return 0

    ok = fail = 0
    t0 = time.time()
    for i, x in enumerate(todo, 1):
        r = gen1shot.gen_one(x["path"])
        if r.get("ok"):
            r.pop("豆包原始", None)
            r["batch"] = x["batch"]
            r["file"] = x["file"]
            data[x["key"]] = r
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f"      失败: {r.get('error', '')[:70]}", flush=True)
        if i % 5 == 0 or i == len(todo):
            el = time.time() - t0
            eta = (el / i) * (len(todo) - i)
            print(f"  [{i}/{len(todo)}] 成功 {ok} 失败 {fail}  "
                  f"已用 {el/60:.1f}min 剩余约 {eta/60:.1f}min", flush=True)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    print()
    print(f"完成: 成功 {ok} / 失败 {fail}")
    print(f"标题已存: {cache}")
    return 0
