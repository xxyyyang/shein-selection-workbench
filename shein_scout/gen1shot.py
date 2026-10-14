# -*- coding: utf-8 -*-
"""
shein_scout/gen1shot.py —— 一次调用生成：标题 + 上架标签 + 标题字段

用户要的流程：
    图 -> 豆包 -> ① 标题
                 ② 上架标签（SHEIN 词库内）
                 ③ 标题文本用的字段

为什么不是"全交给豆包"：
  SHEIN 上架字段必须**一字不差**匹配下拉框，而豆包不知道你的词库
  （它知道"枫叶"，词库写的是"树叶图案"）。
  所以做法是：**把词库塞进提示词让它选，代码再校验纠正**。

为什么要代码校验：
  豆包有一定概率选到词库外的词。代码发现选错时，
  用同义词表兜底；兜不住就留空，**绝不写错词上去**。
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

import requests

from . import titlegen as tg

ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/responses"


def build_prompt() -> str:
    """拼提示词：把 SHEIN 词库和规则都塞进去。"""
    v = tg.load_vocab()
    occ = " ".join(v.get("场合", [])[:60])
    sty = " ".join(v.get("风格", [])[:40])
    pat = " ".join(v.get("图案", [])[:320])
    return f"""你是 SHEIN 家居纺织品的资深运营。看这张**数码印花午休毯/盖毯**的图案图，
输出下面 6 项。只输出 JSON，不要任何解释文字。

【第1项 背景色】
图案的**底色**是什么（不是图案本身的颜色）。必须从这些里选一个：
暖米白 纯白 浅灰 暗黑 午夜深蓝 深绿 深灰 暖焦糖棕 复古卡其 经典大红 粉萌浅粉 明黄 橙红 浅蓝 复古多彩

【第2项 核心图案（含颜色）】
画面中最突出的 1~3 个图案元素，**每个都要带颜色**。
格式："橙色南瓜、白色幽灵、黑色蝙蝠"（用中文顿号分隔，最多 3 个）

【第3项 标题用风格】
2~4 个字的风格氛围词，用于标题文本（**不是**下面那个上架词库）。
例如：秋日温暖 / 搞怪万圣 / 圣诞童趣 / 复古格纹 / 轻奢圣诞 / 清新日常

【第4项 场合标签】
从下面词库选**正好 3 个**最贴合的，按相关度从高到低排，用顿号分隔：
{occ}

【第5项 上架用风格标签】
**必须**从下面词库选**正好 3 个**，按贴合度从高到低排，一字不差照抄：
{sty}

【第6项 上架用细化图案标签】
**必须**从下面词库选**正好 3 个**，按贴合度从高到低排，一字不差照抄
（注意词库用的是"树叶图案"不是"枫叶"，"鬼魂"不是"幽灵"，"饼干"不是"姜饼人"）：
{pat}

【输出格式】严格 JSON：
{{
  "背景色": "暗黑",
  "核心图案": "橙色南瓜、白色幽灵",
  "标题用风格": "搞怪万圣",
  "场合标签": "万圣节、居家、派对",
  "上架用风格标签": "可爱-甜美",
  "上架用细化图案标签": "南瓜、鬼魂"
}}"""


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


def _parse(text: str) -> dict:
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        return json.loads(text[i:j + 1])
    except Exception:
        return {}


def _split(s: str) -> list:
    """切分豆包给的多个词。

    ⚠ 只能用中文顿号/逗号和换行，**不能用 / 和空格** ——
    因为词库里有 『复古绅士/英伦』『休闲-舒适极简』这种带斜杠和空格的词，
    用了就会把词切断（实测踩过）。
    """
    if not s:
        return []
    for sep in ("、", "，", ",", "\n", ";", "；"):
        if sep in s:
            return [x.strip() for x in s.split(sep) if x.strip()]
    return [s.strip()]


def _split_exact(s: str) -> list:
    """给"上架用风格标签"用：只按顿号切，且切完先整体校验一次。"""
    if not s:
        return []
    from . import titlegen as _tg
    lib = _tg.load_vocab().get("风格", [])
    t = s.strip()
    if t in lib:                      # 整串就是词库里的词（含斜杠的情况）
        return [t]
    return _split(s)


def _fix_tags(words: list, kind: str, limit: int = 3) -> list:
    """把豆包给的词纠正到 SHEIN 词库内。纠不了就丢掉。"""
    out = []
    for w in words:
        m = tg.match_vocab(w, kind)
        if m and m not in out:
            out.append(m)
        if len(out) >= limit:
            break
    return out


def gen_one(img: Path, model: str = "", timeout: int = 120,
            verbose: bool = False) -> dict:
    """对一张图生成标题和标签。返回 dict（失败时 ok=False）。"""
    key = os.environ.get("ARK_API_KEY", "").strip()
    if not key:
        return {"ok": False, "error": "未设置 ARK_API_KEY"}
    model = model or os.environ.get("ARK_MODEL") or "doubao-seed-evolving"

    # 压图
    try:
        from PIL import Image
        with Image.open(img) as im:
            im = im.convert("RGB")
            if max(im.size) > 1280:
                s = 1280 / max(im.size)
                im = im.resize((int(im.width * s), int(im.height * s)),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85, optimize=True)
        uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        return {"ok": False, "error": f"读图失败: {e}"}

    payload = {
        "model": model,
        "reasoning": {"effort": "none"},
        "temperature": 0,          # 降随机性，同图同输出
        "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": uri},
            {"type": "input_text", "text": build_prompt()},
        ]}],
    }
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for a in range(3):
        try:
            r = requests.post(ENDPOINT, headers=h, json=payload, timeout=timeout)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:120]}"
                time.sleep(2 * (a + 1))
                continue
            raw = dig_text(r.json())
            d = _parse(raw)
            if not d:
                last = f"解析失败: {raw[:120]}"
                time.sleep(1.5)
                continue
            if verbose:
                print(f"      豆包原始: {json.dumps(d, ensure_ascii=False)[:200]}")

            # ---- 校验并纠正到词库内 ----
            bg = (d.get("背景色") or "").strip()
            core = (d.get("核心图案") or "").strip()
            tstyle = (d.get("标题用风格") or "").strip()
            occ = _fix_tags(_split(d.get("场合标签", "")), "场合", 3)
            sty = _fix_tags(_split_exact(d.get("上架用风格标签", "")), "风格", 3)
            pat = _fix_tags(_split(d.get("上架用细化图案标签", "")), "图案", 3)

            # 从"核心图案"里拆出元素名（用 titlegen 的解析，支持中文顿号）
            pairs = tg.parse_core(core)
            motifs = []
            for c, m in pairs:
                if m and m not in motifs:
                    motifs.append(m)
            if not pat:
                pat = _fix_tags(motifs, "图案", 3)

            # ---- 拼标题（用用户的 V3.1 公式）----
            tags = {
                "节日主题": occ[:1] or ["日常"],
                "背景色": [bg] if bg else [],
                "标题风格": [tstyle] if tstyle else [],
                "画面元素": motifs,
            }
            res = tg.build_title(tags, {"核心图案": core})

            return {
                "ok": True,
                "标题": res["标题"],
                "字数": res["字数"],
                "背景色": bg,
                "核心图案": core,
                "标题用风格": tstyle,
                "上架_场合": occ,
                "上架_风格": sty,
                "上架_图案": pat,
                "豆包原始": d,
            }
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:100]}"
        time.sleep(2 * (a + 1))
    return {"ok": False, "error": last}
