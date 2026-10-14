# -*- coding: utf-8 -*-
"""
用受控词表给图案库的图打标签（替代之前自由发挥的 tagger）。

关键改进：
  * **词表由用户定**（work/标签词表.json），AI 只能从词表里选
  * 词表维度按重要性排序（节日主题 > 配色 > 画风 > 密度 > 元素 > 市场）
  * 标签带英文对照和抓图关键词，能和爬虫/hydrus 打通

用法：
    python tag_controlled.py            # 打全部未标注的
    python tag_controlled.py --limit 5  # 先试 5 张看质量
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "work" / "标签词表.json"
CACHE = ROOT / "work" / "controlled_tags.json"
LIB = ROOT / "patterns" / "library.jsonl"
ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/responses"


def load_vocab() -> dict:
    return json.loads(VOCAB.read_text(encoding="utf-8"))


def build_prompt(vocab: dict) -> str:
    """按词表拼提示词。明确要求：只能选词表里的，不许自己造。"""
    lines = [
        "你是家居纺织品的资深买手。看这张图案/面料图，从下面**给定的选项**里挑标签。",
        "",
        "规则（很重要）：",
        "1. **只能从选项里选，绝对不许自己造标签或写近义词**",
        "2. 每个维度按说明决定选几个；拿不准就少选，不要凑数",
        "3. 只输出 JSON，不要输出任何解释文字",
        "4. JSON 里既要包含下面 8 个维度的标签，",
        "   也要包含后面要求的 核心图案/图案颜色/上架用场合标签/",
        "   上架用风格标签/上架用细化图案标签",
        "",
        "可选项：",
    ]
    for d in vocab["维度"]:
        must = "（必选）" if d.get("必选") else "（可选）"
        note = f"  {d['说明']}" if d.get("说明") else ""
        lines.append(f"\n【{d['名']}】{must}{note}")
        # 带上解释，AI 判断更准
        for t in d["标签"]:
            ex = t.get("解释") or ""
            lines.append(f"  - {t['名']}" + (f"：{ex}" if ex else ""))

    # 顺手生成标题需要的字段（同一次调用，不加钱）
    # 上架填表用的词库（SHEIN 下拉框，必须一字不差）
    shein_sty, shein_pat, shein_occ = [], [], []
    try:
        from . import titlegen as _tg
        vv = _tg.load_vocab()
        shein_sty = vv.get("风格", [])
        shein_pat = vv.get("图案", [])[:300]
        shein_occ = vv.get("场合", [])
    except Exception:
        pass

    lines += [
        "",
        "=" * 40,
        "另外再回答下面几项（生成电商标题 + 上架填表用）：",
        "",
        "【核心图案】画面中最突出的 1~3 个图案元素，**每个都要带颜色**，",
        "  用中文顿号分隔。例：橙色南瓜、白色幽灵、黑色蝙蝠",
        "",
        "【图案颜色】上面那些图案的颜色，用顿号分隔。例：橙、白、黑",
    ]
    # 用户要求：三个词库**各出 3 个**，按跟图片的相似度从高到低排
    if shein_occ:
        lines += [
            "",
            "【上架用场合标签】必须从下面词库选 **正好 3 个**，",
            "  按跟图片的贴合度**从高到低**排，一字不差照抄：",
            "  " + " ".join(shein_occ),
        ]
    if shein_sty:
        lines += [
            "",
            "【上架用风格标签】必须从下面词库选 **正好 3 个**，",
            "  按跟图片的贴合度**从高到低**排，一字不差照抄：",
            "  " + " ".join(shein_sty),
        ]
    if shein_pat:
        lines += [
            "",
            "【上架用细化图案标签】必须从下面词库选 **正好 3 个**，",
            "  按跟图片的贴合度**从高到低**排，一字不差照抄。",
            "  注意词库用的是『树叶图案』不是『枫叶』，『鬼魂』不是『幽灵』，",
            "  『饼干』不是『姜饼人』：",
            "  " + " ".join(shein_pat),
        ]
    lines += ["", "（背景色 和 标题风格 已经在上面选过了，不用重复）"]
    return "\n".join(lines)


def dig_text(d: dict) -> str:
    for it in (d.get("output") or []):
        if not isinstance(it, dict) or it.get("type") == "reasoning":
            continue
        for c in (it.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "output_text":
                t = c.get("text")
                if isinstance(t, str):
                    return t
    return ""


def parse_tags(text: str, vocab: dict) -> dict:
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        obj = json.loads(text[i:j + 1])
    except Exception:
        return {}
    out = {}
    for d in vocab["维度"]:
        allowed = {t["名"] for t in d["标签"]}
        v = obj.get(d["名"])
        if isinstance(v, list):
            hit = [x for x in v if x in allowed]
            if hit or not d.get("必选"):
                out[d["名"]] = hit
        elif isinstance(v, str) and v in allowed:
            out[d["名"]] = [v]

    # 标题用的自由文本字段（不校验词表，原样留）
    for k in ("核心图案", "图案颜色"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()

    # 上架填表字段：**原样保留豆包选的词**（用户要看到 API 输出的词库词）
    for k in ("上架用场合标签", "上架用风格标签", "上架用细化图案标签"):
        v = obj.get(k)
        if isinstance(v, list):
            out[k] = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def tag_one(path: Path, vocab: dict, model: str, prompt: str) -> dict:
    key = os.environ.get("ARK_API_KEY", "").strip()
    if not key:
        return {"ok": False, "error": "未设置 ARK_API_KEY"}
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > 1280:
                s = 1280 / max(im.size)
                im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85, optimize=True)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return {"ok": False, "error": f"读图失败: {e}"}

    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": uri},
            {"type": "input_text", "text": prompt},
        ]}],
    }
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for a in range(3):
        try:
            r = requests.post(ENDPOINT, headers=h, json=payload, timeout=120)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:120]}"
                time.sleep(2 * (a + 1))
                continue
            tags = parse_tags(dig_text(r.json()), vocab)
            if tags:
                return {"ok": True, "tags": tags, "raw": dig_text(r.json())[:400]}
            last = "没解析出标签"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:100]}"
        time.sleep(2 * (a + 1))
    return {"ok": False, "error": last}


def img_path(root: Path, batch: str, rel: str, fname: str) -> Path:
    base = root / "patterns" / batch
    r = (rel or fname or "").replace("\\", "/").lstrip("/")
    parts = r.split("/")
    while parts and parts[0] == batch:
        parts = parts[1:]
    if len(parts) > 1:
        return base.joinpath(*parts)
    for sub in sorted(base.iterdir()) if base.exists() else []:
        if sub.is_dir() and (sub / parts[0]).exists():
            return sub / parts[0]
    return base / parts[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo", action="store_true", help="重打已有标签的")
    args = ap.parse_args()

    if not os.environ.get("ARK_API_KEY"):
        print("请先设置 ARK_API_KEY")
        return 1

    vocab = load_vocab()
    prompt = build_prompt(vocab)
    print(f"词表: {len(vocab['维度'])} 个维度，"
          f"{sum(len(d['标签']) for d in vocab['维度'])} 个标签")
    print(f"提示词 {len(prompt)} 字符\n")

    recs = []
    if LIB.exists():
        for line in LIB.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    print(f"图案库 {len(recs)} 条")

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    todo = []
    for r in recs:
        k = f"{r.get('batch')}/{r.get('rel') or r.get('file')}"
        if k in cache and not args.redo:
            continue
        p = img_path(ROOT, r.get("batch", ""), r.get("rel", ""), r.get("file", ""))
        if p.exists():
            todo.append((k, p))
    if args.limit:
        todo = todo[:args.limit]
    print(f"待打标签 {len(todo)} 张\n")

    ok = fail = 0
    t0 = time.time()
    for i, (k, p) in enumerate(todo, 1):
        res = tag_one(p, vocab, os.environ.get("ARK_MODEL") or "doubao-seed-evolving",
                      prompt)
        if res["ok"]:
            cache[k] = res["tags"]
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f"    失败: {res['error'][:70]}")
        if i % 5 == 0 or i == len(todo):
            el = time.time() - t0
            print(f"  [{i}/{len(todo)}] 成功 {ok} 失败 {fail}  "
                  f"已用 {el/60:.1f}min  剩余约 {(el/i)*(len(todo)-i)/60:.1f}min")
            CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完成: 成功 {ok} / 失败 {fail}")
    print(f"标签已存: {CACHE}")
    return 0


def run(root: Path = None, limit: int = 0, redo: bool = False,
        verbose: bool = True) -> int:
    """供 CLI / GUI 调用。返回 0 表示成功。"""
    global ROOT
    if root:
        ROOT = Path(root)
    sys.argv = ["tagctl"] + (["--limit", str(limit)] if limit else []) + \
               (["--redo"] if redo else [])
    return main()


if __name__ == "__main__":
    sys.exit(main())
