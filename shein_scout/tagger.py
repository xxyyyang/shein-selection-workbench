# -*- coding: utf-8 -*-
"""
shein_scout.tagger —— AI 给图案打标签

设计要点（按用户明确的要求）：
  * **节日/主题是第一维度** —— 爬图时就指定，同主题内才比审美
  * **配色风格是第二维度** —— 同主题内的主要排序依据
  * 其余维度（密度/画风/元素/适用季节）作为细分，量大了再用
  * 标签**有固定取值表**（不是让 AI 自由发挥），这样标签可统计、可加权
  * 前期标签给得多，等你的偏好稳定了自动收窄（只问相关的）

复用：豆包视觉接口已有可用实现（shein_scout.vlm）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

# ------------------------------------------------------------------ 标签体系
# 第一维度：节日/主题（硬分区）。爬图时用关键词指定，AI 只做归属确认。
THEME = [
    "万圣节", "秋季感恩节", "圣诞节", "情人节", "复活节", "母亲节", "父亲节",
    "独立日", "春季", "夏季", "冬季", "新年",
    "动物宠物", "花卉植物", "几何抽象", "格纹拼布", "卡通动漫", "文字标语",
    "星空宇宙", "海洋沙滩", "城市建筑", "食物饮品", "四季通用",
]

# 第二维度：配色风格（同主题内的主要排序依据）
COLOR = [
    "暖橙红", "暖黄棕", "大地色", "莫兰迪冷", "莫兰迪暖", "马卡龙粉",
    "黑白灰", "蓝绿冷", "紫色系", "红色系", "粉色系", "多彩撞色",
    "低饱和素雅", "高饱和鲜艳", "复古做旧",
]

# 第三维度：细分（前期都给，偏好稳定后收窄）
DENSITY = ["满版密铺", "中等疏密", "大面积留白", "单一大图案", "小碎花"]
STYLE = ["写实", "手绘插画", "卡通可爱", "水彩", "线稿", "扁平矢量",
         "拼布格子", "复古版画"]
ELEMENT = ["文字标语", "边框花纹", "格纹底", "条纹底", "渐变", "光斑", "无"]
SEASON = ["秋季", "冬季", "春季", "夏季", "四季通用"]

ALL_DIMS = {
    "节日主题": THEME,
    "配色风格": COLOR,
    "图案密度": DENSITY,
    "画风": STYLE,
    "画面元素": ELEMENT,
    "适用季节": SEASON,
}

# 维度优先级：主题 > 配色 > 其他（用户明确要求节日/主题占大权重）
DIM_WEIGHT = {
    "节日主题": 3.0,
    "配色风格": 2.0,
    "适用季节": 1.5,
    "画风": 1.0,
    "图案密度": 1.0,
    "画面元素": 0.8,
}

PROMPT_HEAD = """你是家居纺织品的资深买手。看这张图案/面料图，从下面的**固定选项**里挑出符合的标签。

规则：
- 每个维度可以多选，但**只选图上真实存在的**，不要脑补
- 拿不准就少选，不要凑数
- 节日主题里，如果是明确的节日图案就选那个节日；如果只是通用图案（比如纯几何、纯花卉），选对应类别或"四季通用"
- 只输出 JSON，格式：{"节日主题":[...],"配色风格":[...],"图案密度":[...],"画风":[...],"画面元素":[...],"适用季节":[...]}

可选标签：
"""


def build_prompt(dims: dict[str, list[str]] | None = None) -> str:
    dims = dims or ALL_DIMS
    lines = [PROMPT_HEAD]
    for d, vals in dims.items():
        lines.append(f"{d}: {' / '.join(vals)}")
    return "\n".join(lines)


def _dig(d: dict) -> str:
    """从 Responses API 返回里取答案（跳过 reasoning）。"""
    for it in (d.get("output") or []):
        if not isinstance(it, dict) or it.get("type") == "reasoning":
            continue
        for c in (it.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "output_text":
                t = c.get("text")
                if isinstance(t, str):
                    return t
    return ""


def _parse(text: str, dims: dict[str, list[str]]) -> dict:
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        obj = json.loads(text[i:j + 1])
    except Exception:
        return {}
    out = {}
    for d, allowed in dims.items():
        v = obj.get(d)
        if isinstance(v, list):
            # 只保留在候选表里的，防止 AI 自由发挥
            out[d] = [x for x in v if x in allowed]
        elif isinstance(v, str) and v in allowed:
            out[d] = [v]
    return out


def tag_image(path: str | Path, model: str = "", cache_dir: Path | None = None,
              dims: dict[str, list[str]] | None = None, retries: int = 3) -> dict:
    """给一张图打标签。返回 {ok, tags:{维度:[标签]}, error, cached}"""
    import base64
    import io

    import requests
    from PIL import Image

    dims = dims or ALL_DIMS
    model = model or os.environ.get("ARK_MODEL", "") or "doubao-seed-evolving"
    key = os.environ.get("ARK_API_KEY", "").strip()
    if not key:
        return {"ok": False, "tags": {}, "error": "未设置 ARK_API_KEY"}

    p = Path(path)
    cache_dir = cache_dir or (Path(__file__).resolve().parent.parent /
                              "work" / "tag_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        st = p.stat()
        _h = abs(hash((str(p.resolve()), st.st_size, int(st.st_mtime), model)))
        ck = cache_dir / f"{_h}.json"
    except Exception:
        ck = cache_dir / f"{abs(hash(str(path)))}.json"
    if ck.exists():
        try:
            d = json.loads(ck.read_text(encoding="utf-8"))
            d["cached"] = True
            return d
        except Exception:
            pass

    try:
        with Image.open(p) as im:
            im = im.convert("RGB")
            if max(im.size) > 1280:
                s = 1280 / max(im.size)
                im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85, optimize=True)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return {"ok": False, "tags": {}, "error": f"读图失败: {e}"}

    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": uri},
            {"type": "input_text", "text": build_prompt(dims)},
        ]}],
    }
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for a in range(retries):
        try:
            r = requests.post(
                "https://ark.cn-beijing.volces.com/api/v3/responses",
                headers=h, json=payload, timeout=120)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:160]}"
                time.sleep(1.5 * (a + 1))
                continue
            tags = _parse(_dig(r.json()), dims)
            if tags:
                out = {"ok": True, "tags": tags, "error": "", "cached": False}
                ck.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
                return out
            last = "返回里没有可解析的标签"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
        time.sleep(1.5 * (a + 1))
    return {"ok": False, "tags": {}, "error": last}


def tag_many(recs: list, root: Path, model: str = "", verbose: bool = True,
             limit: int = 0) -> dict:
    """给一批记录打标签。recs 是 library.Rec 列表。就地更新并返回统计。"""
    todo = [r for r in recs if not r.tags]
    if limit:
        todo = todo[:limit]
    n_have = sum(1 for r in recs if r.tags)
    if verbose:
        print(f"  本批 {len(recs)} 张：已有标签 {n_have} 张，本次打 {len(todo)} 张")
    ok = fail = 0
    t0 = time.time()
    for i, r in enumerate(todo, 1):
        from . import library as _lib
        p = _lib.img_path(root, r.batch, r.rel, r.file)
        if not p.exists():
            fail += 1
            continue
        res = tag_image(p, model=model)
        if res["ok"]:
            r.tags = res["tags"]
            ok += 1
        else:
            fail += 1
            if verbose and fail <= 3:
                print(f"      失败: {res['error'][:70]}")
        if verbose and (i % 10 == 0 or i == len(todo)):
            el = time.time() - t0
            eta = (el / i) * (len(todo) - i)
            print(f"      [{i}/{len(todo)}] 成功 {ok} 失败 {fail}  "
                  f"已用 {el/60:.1f}min  剩余约 {eta/60:.1f}min")
    return {"ok": ok, "fail": fail, "total": len(todo)}
