# -*- coding: utf-8 -*-
"""
shein_scout/titlegen.py —— 按 SHEIN 风格生成电商标题

流程（用户 V3.1 公式）：
  1件 - 场合 - 修饰背景色 - 工艺词 - 核心图案 - 印花方式 - 风格 - 盖毯 - 材质描述 - 适用场景

三类字段来源不同，**不能混**：
  · 标题文本用：场合（自由，如"秋季"）、风格（如"秋日温暖"）
  · 上架填表用：场合/风格/细化图案 —— **必须精确匹配 SHEIN 词库**
  · 固定信息：1件 / 无缝印花 / 盖毯 / 柔软蓬松超细纤维 / 沙发客厅卧室派对

词库位置：C:\\图片\\提示词\\shein\\{场合,风格,细化图案}.txt
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# 词库目录：优先读环境变量 / 配置，读不到用数据根下的默认位置。
# 迁移到别的机器时，只要把词库放好或设 DSH_SHEIN_VOCAB 环境变量即可。
import os as _os


def _vocab_dir() -> Path:
    """按 环境变量 > 配置 > 数据根默认 的顺序定位 SHEIN 词库目录。"""
    env = _os.environ.get("DSH_SHEIN_VOCAB", "").strip()
    if env:
        return Path(env)
    try:
        from .paths import VOCAB_DIR as _v  # 统一由 paths.py 决定
        if str(_v):
            return Path(_v)
    except Exception:
        pass
    return Path("data") / "SHEIN词库"


VOCAB_DIR = _vocab_dir()

# ---------------- 固定信息 ----------------
FIXED = {
    "数量": "1件",
    "印花方式": "无缝印花",
    "主体": "盖毯",
    "材质": "柔软蓬松超细纤维",
    "适用场景": "沙发客厅卧室派对",
}

# ---------------- 标题用：节日 → 场合引领词 ----------------
# 词库里没有"秋季"，但标题里用"秋季"更好搜，这里分开
THEME_TO_OCCASION = {
    "万圣节": "万圣节", "圣诞节": "圣诞节", "感恩节": "感恩节",
    "情人节": "情人节", "母亲节": "母亲节", "父亲节": "父亲节",
    "复活节": "复活节", "新年": "新年",
    # 用户：秋季意味着丰收意味着感恩。SHEIN 场合词库没有"秋季"，
    # 所以标题引领词用词库里真实存在的"丰收节"（感恩节也在词库）
    "秋季": "丰收节",
    "斋月": "斋月", "开斋节": "开斋节", "宰牲节": "宰牲节",
    "日常": "居家", "花卉": "居家", "动物": "居家",
    "几何": "居家", "格纹": "居家", "卡通": "居家",
}

# ---------------- 标题用：画风 → 工艺词 ----------------
STYLE_TO_CRAFT = {
    "手绘插画": "水彩", "水彩": "水彩",
    "卡通可爱": "卡通", "扁平矢量": "简约",
    "线稿": "手绘", "复古版画": "复古",
    "拼布格子": "格纹", "几何图形": "几何",
    "写实": "写实",
}
# 图案元素对工艺词的覆盖（更准）
MOTIF_CRAFT = {
    "幽灵": "涂鸦", "南瓜": "卡通", "雪花": "针织", "驯鹿": "针织",
    "姜饼人": "针织", "拐杖糖": "针织", "月亮星星": "烫金",
    "枫叶": "水彩", "格纹底": "格纹", "阿拉伯纹样": "烫金",
}

# ---------------- SHEIN 词库加载 ----------------
_cache: dict = {}


def load_vocab() -> dict:
    """读 SHEIN 的三个词库。"""
    if _cache:
        return _cache
    for key, fn in (("场合", "场合.txt"), ("风格", "风格.txt"),
                    ("图案", "细化图案.txt")):
        p = VOCAB_DIR / fn
        try:
            words = p.read_text(encoding="utf-8").split()
            seen, out = set(), []
            for w in words:
                if w and w not in seen:
                    seen.add(w)
                    out.append(w)
            _cache[key] = out
        except Exception:
            _cache[key] = []
    return _cache


def match_vocab(word: str, kind: str) -> str:
    """把 AI 给的词匹配到 SHEIN 词库里的词。匹配不上返回空串。

    顺序很重要：
      1) 完全一致 -> 直接用
      2) 同义词表 -> 精确映射（最可靠）
      3) 包含匹配 -> 但要求**长度接近**，避免 '雪花' 撞上 '雪花动漫'
    """
    lib = load_vocab().get(kind, [])
    if not lib or not word:
        return ""
    # 1) 完全一致
    if word in lib:
        return word
    # 2) 同义词表（精确）
    syn = SYNONYMS.get(kind, {}).get(word)
    if syn:
        return syn if syn in lib else ""
    # 3) 包含匹配：只在长度差 <= 2 时才认，避免误撞
    cands = [w for w in lib if (word in w or w in word)
             and abs(len(w) - len(word)) <= 2]
    if cands:
        return min(cands, key=len)
    return ""


# 同义词表：AI 标签 -> SHEIN 词库用词
SYNONYMS = {
    "图案": {
        "枫叶": "树叶图案", "树叶": "树叶图案", "松果": "树叶图案",
        "干枝": "树叶图案", "树枝": "树叶图案", "秋叶": "树叶图案",
        "雪花": "圣诞节", "雪人": "雪人", "麋鹿": "麋鹿",
        "蝙蝠": "蝙蝠", "幽灵": "鬼魂", "鬼魂": "鬼魂",
        "南瓜": "南瓜", "南瓜灯": "南瓜灯",
        "拐杖糖": "糖果", "姜饼人": "饼干",
        "圣诞树": "圣诞树", "圣诞老人": "圣诞老人", "圣诞袜": "圣诞袜",
        "铃铛": "铃铛", "月亮星星": "星星", "月亮": "月亮",
        "爱心": "心", "灯笼": "灯笼",
        "格纹底": "格纹", "千鸟格": "千鸟格", "棋盘格": "棋盘格",
        "文字标语": "字母", "字母": "字母", "标语": "标语",
        "圆点": "圆点", "条纹底": "条纹",
        "渐变": "渐变色", "花朵": "花朵印花", "花卉": "花朵印花",
        "阿拉伯纹样": "民族风", "几何图形": "几何图案",
        "边框花纹": "几何图案", "光斑": "亮闪闪",
        "卡通可爱": "卡通", "手绘插画": "插画", "水彩": "水彩",
        "扁平矢量": "简笔", "线稿": "简笔", "复古版画": "版画印花",
        "写实": "绘图", "拼布格子": "格子",
        "满版密铺": "全身印花", "单一大图案": "定位印花",
        "小碎花": "小碎花", "大面积留白": "纯色",
        "多彩撞色": "撞色", "高饱和鲜艳": "撞色",
        "太阳": "太阳", "云朵": "云朵", "星星": "星星",
        "彩虹": "彩虹", "心": "心", "骷髅": "骷髅",
    },
    "场合": {
        "万圣节": "万圣节", "圣诞节": "圣诞节", "感恩节": "感恩节",
        "情人节": "情人节", "母亲节": "母亲节", "父亲节": "父亲节",
        "复活节": "复活节", "新年": "新年", "居家": "居家",
        "日常": "日常", "派对": "派对", "秋季": "",   # 词库无秋季，留空
        "斋月": "", "开斋节": "开斋节", "宰牲节": "宰牲节",
        "花卉": "居家", "动物": "居家", "几何": "居家",
        "格纹": "居家", "卡通": "其他", "文字": "其他",
        "星空": "其他", "海洋": "度假", "食物": "居家",
    },
    "风格": {
        "秋日温暖": "休闲-舒适极简", "秋日复古": "复古绅士/英伦",
        "秋日童趣": "可爱-甜美", "简约秋日": "极简",
        "搞怪万圣": "可爱-甜美", "趣味万圣": "可爱-甜美",
        "欢乐万圣": "可爱-甜美", "暗黑万圣": "小众-哥特",
        "圣诞童趣": "可爱-甜美", "圣诞复古": "复古绅士/英伦",
        "圣诞可爱": "可爱-甜美", "轻奢圣诞": "轻奢风",
        "粉萌圣诞": "可爱-甜美", "简约圣诞": "极简",
        "经典圣诞": "传统的", "复古格纹": "复古绅士/英伦",
        "复古红格": "复古绅士/英伦", "清新日常": "休闲-舒适极简",
        "温柔居家": "休闲-舒适极简", "复古侘寂": "森系风格",
        "轻奢金调": "轻奢风", "童趣可爱": "可爱-甜美",
        "静谧夜色": "小众-哥特", "活力撞色": "嘻哈",
        "手绘插画": "森系风格", "水彩": "森系风格",
        "卡通可爱": "可爱-甜美", "扁平矢量": "极简",
        "线稿": "极简", "复古版画": "复古绅士/英伦",
        "几何图形": "现代风", "拼布格子": "Preppy学院",
        "写实": "其他",
    },
}


def match_many(words: list, kind: str, limit: int = 3) -> list:
    """多个词匹配词库，去重，限量。"""
    out = []
    for w in words:
        m = match_vocab(w, kind)
        if m and m not in out:
            out.append(m)
        if len(out) >= limit:
            break
    return out


# ---------------- 颜色处理 ----------------
# 颜色词（正则用，**长的排前面**，避免"紫红色"被拆成"紫"+"色枫叶"）
_COLOR_UNIT = (
    "紫红色|橙红色|酒红色|深蓝色|深绿色|浅蓝色|浅粉色|粉红色|"
    "红色|橙色|黄色|绿色|蓝色|紫色|粉色|黑色|白色|灰色|棕色|金色|银色|米色|"
    "大红|深蓝|深绿|浅蓝|浅粉|橙红|紫红|暗红|藏青|焦糖|卡其|"
    "红|橙|黄|绿|蓝|紫|粉|黑|白|灰|棕|金|银|米|彩"
)
_COLOR_RE = re.compile("^(?:" + _COLOR_UNIT + ")+")


def split_color_motif(phrase: str) -> tuple:
    """把「红色枫叶」拆成 ('红', '枫叶')，「红绿白拐杖糖」-> ('红绿白', '拐杖糖')。

    做法：正则**贪心**吃掉开头所有颜色字，剩下的就是图案名。
    """
    p = (phrase or "").strip()
    if not p:
        return "", ""
    m = _COLOR_RE.match(p)
    if not m:
        return "", p
    raw = m.group(0)
    rest = p[len(raw):]
    if not rest:                     # 整个词都是颜色，没有图案名
        return raw.replace("色", ""), ""
    # 颜色里去掉"色"字：红色->红, 橙红色->橙红, 红绿白->红绿白
    color = re.sub(r"色(?![\u4e00-\u9fff])", "", raw)
    color = color.replace("色", "")
    return color, rest.strip()


def _same_base(a: str, b: str) -> bool:
    """两个图案名是不是**同一个东西**。

    算同一个：枫叶 == 枫叶；枫叶 / 枫叶图案（共享前2字且长度接近）
    不算同一个：橙南瓜 vs 白幽灵；圣诞树饼干 vs 星星饼干（是不同图案）
    """
    if not a or not b:
        return False
    if a == b:
        return True
    return a[:2] == b[:2] and abs(len(a) - len(b)) <= 2


def color_count_phrase(pairs: list) -> str:
    """按用户的颜色数量规则拼核心图案。

    规则（从用户 28 条记录反推）：
      · 同一底层图案的多个颜色 **合并** -> 红紫红橙红枫叶
      · 不同图案 **各自带色** -> 橙南瓜白幽灵
      · 4 色以上 -> 多色秋叶
    """
    if not pairs:
        return "印花"

    colors = []
    for c, _ in pairs:
        if c and c not in colors:
            colors.append(c)
    ncol = len(colors)

    # 按底层图案归组
    groups = []
    for c, m in pairs:
        if not m:
            continue
        hit = None
        for g in groups:
            if _same_base(m, g[0]):
                hit = g
                break
        if hit:
            if c and c not in hit[1]:
                hit[1].append(c)
        else:
            groups.append([m, [c] if c else []])
    if not groups:
        return "印花"

    if ncol >= 4:
        return "多色" + groups[0][0]

    # 单色：每个图案各自带色
    if ncol == 1:
        return "".join((cs[0] if cs else "") + base for base, cs in groups)

    # 2~3 色
    if len(groups) == 1:
        return "".join(groups[0][1]) + groups[0][0]
    return "".join(("".join(cs) if cs else "") + base for base, cs in groups)


def parse_core(core: str) -> list:
    """把豆包给的「橙色南瓜、白色幽灵」解析成 [(颜色, 图案), ...]。"""
    if not core:
        return []
    for sep in ("、", "，", ",", "/", "；", ";", "|"):
        core = core.replace(sep, "\n")
    out = []
    for part in core.split("\n"):
        part = part.strip()
        if not part:
            continue
        c, m = split_color_motif(part)
        if m or c:
            out.append((c, m or part))
    return out


def parse_core(core: str) -> list:
    """把豆包给的「橙色南瓜、白色幽灵」解析成 [(颜色, 图案), ...]。"""
    if not core:
        return []
    for sep in ("、", "，", ",", "/", "；", ";", "|"):
        core = core.replace(sep, "\n")
    out = []
    for part in core.split("\n"):
        part = part.strip()
        if not part:
            continue
        c, m = split_color_motif(part)
        if m or c:
            out.append((c, m or part))
    return out


def parse_core(core: str) -> list:
    """把豆包给的「橙色南瓜、白色幽灵」解析成 [(颜色, 图案), ...]。"""
    if not core:
        return []
    for sep in ("、", "，", ",", "/", "；", ";", "|"):
        core = core.replace(sep, "\n")
    out = []
    for part in core.split("\n"):
        part = part.strip()
        if not part:
            continue
        c, m = split_color_motif(part)
        if m or c:
            out.append((c, m or part))
    return out


def pick_occasion_lead(theme: str, shein_occ: list) -> str:
    """标题引领词。

    用户要求：**用 SHEIN 词库里真实存在的词**，不要自己造
    （例：秋季 -> 丰收节，因为词库没有"秋季"）。

    优先级：主题映射词 > SHEIN 场合词 > 居家
    """
    mapped = THEME_TO_OCCASION.get(theme, "")
    if mapped and mapped != "其他":
        return mapped
    for o in (shein_occ or []):
        if o and o != "其他":
            return o
    return "居家"


def build_title(tags: dict, extra: dict = None) -> dict:
    """按 V3.1 公式拼标题。

    tags 形如 {"节日主题": ["万圣节"], "背景色": ["暗黑"], ...}
    返回 {"标题", "字数", "上架_场合", "上架_风格", "上架_图案"}
    """
    extra = extra or {}

    def get(dim):
        return list(tags.get(dim) or [])

    theme = (get("节日主题") or ["日常"])[0]
    bg = (get("背景色") or ["暖米白"])[0]
    craft_src = (get("画风") or [""])[0]
    tstyle = (get("标题风格") or [""])[0]
    motifs = get("画面元素")[:3]

    # 上架场合（先算，标题里要用它兜底）
    shein_occ = match_many([theme] + get("节日主题"), "场合", 3)
    occasion = pick_occasion_lead(theme, shein_occ)

    # 工艺词：优先按图案元素定
    craft = ""
    for m in motifs:
        if m in MOTIF_CRAFT:
            craft = MOTIF_CRAFT[m]
            break
    craft = craft or STYLE_TO_CRAFT.get(craft_src, "简约")

    # 核心图案：优先用豆包给的原文（信息最全），按颜色规则压缩
    core_raw = extra.get("核心图案") or ""
    pairs = parse_core(core_raw)
    if pairs:
        core = color_count_phrase(pairs)
        # 如果压缩后太短（只剩一个图案名），退回原文
        if len(core) < 4 and len(core_raw) > len(core):
            core = core_raw.replace("、", "")
    else:
        core = color_count_phrase([("", m) for m in motifs]) if motifs else "印花图案"

    parts = [
        FIXED["数量"], occasion, bg + "底色", craft, core,
        FIXED["印花方式"], tstyle or "简约", FIXED["主体"], FIXED["材质"],
        FIXED["适用场景"],
    ]
    title = "-".join(p for p in parts if p)

    return {
        "标题": title,
        "字数": len(title),
        "上架_场合": shein_occ,
        "上架_风格": match_many([tstyle] + get("画风"), "风格", 3),
        "上架_图案": (match_many(motifs, "图案", 3)
                      or _as_list(tags.get("上架用细化图案标签"))[:3]),
    }


def _as_list(v):
    if not v:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).replace("、", ",").split(",") if x.strip()]


def build_variants(tags: dict, n: int = 8) -> list:
    """生成多个**真正不同**的标题变体。

    上架字段（场合/风格/图案）保持不变，只改标题文本的侧重。
    """
    base = build_title(tags)
    ps = base["标题"].split("-")
    if len(ps) < 8:
        return [base]

    def mk(parts, name):
        parts = [p for p in parts if p]
        out, seen = [], set()
        for p in parts:          # 去掉相邻重复
            if p not in seen:
                out.append(p)
                seen.add(p)
        t = "-".join(out)
        return {"标题": t, "字数": len(t), "变体": name,
                "上架_场合": base["上架_场合"], "上架_风格": base["上架_风格"],
                "上架_图案": base["上架_图案"]}

    # ps: 0数量 1场合 2底色 3工艺 4核心图案 5印花 6风格 7盖毯 8材质 9场景
    motifs = (tags.get("画面元素") or [])[:3]
    m1 = motifs[0] if motifs else ps[4]
    m2 = motifs[1] if len(motifs) > 1 else m1
    theme = ps[1]

    variants = [mk(ps, "标准版")]
    # 只突出主元素
    variants.append(mk([ps[0], theme, ps[2], m1, ps[5], ps[6], ps[7]], "单一主元素"))
    # 突出两个元素的组合
    variants.append(mk([ps[0], theme, ps[2], ps[3], m1 + m2, ps[5], ps[6], ps[7],
                        ps[8]], "双元素组合"))
    # 极简
    variants.append(mk([ps[0], theme, m1, ps[6], ps[7]], "极简版"))
    # 强调满版印花工艺
    variants.append(mk([ps[0], theme, ps[2], "满版" + ps[5], ps[4], ps[6], ps[7],
                        ps[9]], "强调满版"))
    # 强调氛围（风格词前置）
    variants.append(mk([ps[0], ps[6], theme, ps[2], ps[4], ps[5], ps[7], ps[8],
                        ps[9]], "氛围前置"))
    # 强调材质手感
    variants.append(mk([ps[0], theme, ps[2], ps[4], ps[5], ps[6], ps[7],
                        "亲肤" + ps[8], ps[9]], "强调材质"))
    # 全场景
    variants.append(mk([ps[0], theme, ps[2], ps[3], ps[4], ps[5], ps[6], ps[7],
                        ps[8], "沙发客厅卧室派对旅行办公"], "全场景"))
    return variants[:n]

# ---------------------------------------------------------------- 组合生成
def combine_titles(tags: dict, core: str = "", n: int = 12) -> list:
    """**不调 API**，用已选标签组合出多个标题供挑选。

    用户要求："直接组合供我挑选"。
    做法：固定 10 个位置里可变的几个（场合/工艺/核心图案/风格），
          其余固定，做笛卡尔积的**抽样**，并按可读性排序。
    """
    theme = (tags.get("节日主题") or ["日常"])[0]
    bgs = tags.get("背景色") or ["暖米白"]
    crafts = []
    for m in (tags.get("画面元素") or [])[:3]:
        if m in MOTIF_CRAFT:
            crafts.append(MOTIF_CRAFT[m])
    for st in (tags.get("画风") or [])[:2]:
        if st in STYLE_TO_CRAFT:
            crafts.append(STYLE_TO_CRAFT[st])
    crafts = list(dict.fromkeys(crafts)) or ["简约"]
    tstyles = tags.get("标题风格") or ["简约"]
    motifs = tags.get("画面元素") or []

    shein_occ = match_many([theme] + (tags.get("节日主题") or []), "场合", 3)
    lead = pick_occasion_lead(theme, shein_occ)

    # 核心图案的几种写法
    cores = []
    if core:
        cores.append(core)
    if motifs:
        cores.append("".join(motifs[:3]))
        cores.append(motifs[0])
        if len(motifs) >= 2:
            cores.append(motifs[0] + motifs[1])
    cores = [c for c in dict.fromkeys(cores) if c] or ["印花图案"]

    out = []
    for bg in bgs[:3]:
        for craft in crafts[:3]:
            for ts in tstyles[:3]:
                for c in cores[:3]:
                    parts = [FIXED["数量"], lead, bg + "底色", craft, c,
                             FIXED["印花方式"], ts, FIXED["主体"],
                             FIXED["材质"], FIXED["适用场景"]]
                    t = "-".join(p for p in parts if p)
                    if len(t) <= 200 and t not in [x["标题"] for x in out]:
                        out.append({
                            "标题": t, "字数": len(t),
                            "上架_场合": shein_occ,
                            "上架_风格": match_many(tstyles + (tags.get("画风") or []), "风格", 3),
                            "上架_图案": match_many(motifs, "图案", 3),
                        })
                    if len(out) >= n * 3:
                        break
    # 排序：字数接近 55~75 的排前面（SHEIN 标题长度甜区）
    out.sort(key=lambda x: abs(x["字数"] - 65))
    return out[:n]

def title_from_tags(tags: dict) -> dict:
    """**不调 API**，直接用已存的标签出标题。

    核心图案取值优先级（越靠前越好）：
      1) tags['核心图案']            —— 豆包给的带色描述
      2) tags['上架用细化图案标签']    —— SHEIN 词库词，拼起来当图案名
      3) tags['画面元素']            —— 标签里的元素
      4) "印花图案"                 —— 兜底
    """
    def first(k):
        v = tags.get(k)
        if isinstance(v, list):
            return (v[0] if v else "")
        return v or ""

    core = first("核心图案")
    if core:
        pairs = parse_core(core)
        if pairs:
            core = color_count_phrase(pairs)
    else:
        # 回退：用 SHEIN 细化图案词
        pats = tags.get("上架用细化图案标签") or []
        if isinstance(pats, str):
            pats = [p for p in pats.replace("、", ",").split(",") if p.strip()]
        motifs = tags.get("画面元素") or []
        use = pats or motifs
        if use:
            # 用配色给它们上色（如 配色=['暖橙红'] -> 橙）
            col = first("配色")
            c1 = ""
            for c in list("红橙黄绿蓝紫粉黑白灰棕金银米"):
                if c in (col or ""):
                    c1 = c
                    break
            core = "".join(use[:2])
            if c1 and not core.startswith(c1):
                core = c1 + core

    clean = {k: v for k, v in tags.items()
             if k not in ("核心图案", "图案颜色")}
    return build_title(clean, {"核心图案": core})


def title_variants_from_tags(tags: dict, n: int = 8) -> list:
    """用标签出多个标题变体（本地，不调 API）。"""
    base = title_from_tags(tags)
    ps = base["标题"].split("-")
    if len(ps) < 8:
        return [base]
    motifs = tags.get("画面元素") or []
    m1 = motifs[0] if motifs else ps[4]

    def mk(parts, name):
        out, seen = [], set()
        for x in parts:
            if x and x not in seen:
                out.append(x)
                seen.add(x)
        t = "-".join(out)
        return {"标题": t, "字数": len(t), "变体": name,
                "上架_场合": base["上架_场合"], "上架_风格": base["上架_风格"],
                "上架_图案": base["上架_图案"]}

    return [
        dict(base, 变体="标准版"),
        mk([ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6], ps[7]], "精简"),
        mk([ps[0], ps[1], m1, ps[6], ps[7]], "只留主元素"),
        mk([ps[0], ps[1], ps[2], m1, ps[5], ps[6], ps[7]], "无工艺词"),
        mk([ps[0], ps[6], ps[1], ps[2], ps[4], ps[5], ps[7], ps[9]], "氛围前置"),
    ][:n]


# ---------------------------------------------------------------- 多标签组合查询

# 中文标签 -> 英文搜图词（用于组合查询）
TAG_EN_TEXT = {
    "万圣节": "halloween", "秋季": "fall autumn", "感恩节": "thanksgiving",
    "圣诞节": "christmas", "情人节": "valentine", "复活节": "easter",
    "母亲节": "mothers day", "父亲节": "fathers day", "独立日": "4th july",
    "斋月": "ramadan", "开斋节": "eid", "宰牲节": "eid adha",
    "新年": "new year", "动物": "animal", "花卉": "floral",
    "几何": "geometric", "格纹": "plaid", "卡通": "cartoon",
    "文字": "typography", "星空": "celestial", "海洋": "ocean",
    "食物": "food", "日常": "minimal",
    "南瓜": "pumpkin", "幽灵": "ghost", "蝙蝠": "bat", "骷髅": "skull",
    "枫叶": "maple leaf", "松果": "pinecone", "雪花": "snowflake",
    "驯鹿": "reindeer", "雪人": "snowman", "拐杖糖": "candy cane",
    "姜饼人": "gingerbread", "圣诞树": "christmas tree", "铃铛": "bell",
    "月亮星星": "moon star", "灯笼": "lantern", "爱心": "heart",
    "阿拉伯纹样": "arabesque", "文字标语": "typography", "边框花纹": "border",
    "格纹底": "plaid", "条纹底": "stripe", "渐变": "gradient",
    "光斑": "bokeh sparkle", "南瓜灯": "jack o lantern",
    "蜘蛛网": "spider web", "糖果": "candy", "饼干": "cookie",
    "暖橙红": "warm orange", "暖黄棕": "warm brown", "大地色": "earth tone",
    "金棕": "gold brown", "黑白灰": "black white", "蓝绿冷": "teal blue",
    "绿白": "green white", "红金": "red gold", "粉萌浅粉": "pastel pink",
    "低饱和素雅": "soft muted", "高饱和鲜艳": "vivid bright",
    "多彩撞色": "colorful", "紫色系": "purple", "莫兰迪冷": "muted blue grey",
    "手绘插画": "watercolor hand drawn", "水彩": "watercolor",
    "卡通可爱": "cute cartoon", "扁平矢量": "flat vector",
    "线稿": "line art", "复古版画": "vintage print",
    "几何图形": "geometric graphic", "拼布格子": "patchwork",
    # 背景色（标题字段里用的）
    "暗黑": "black", "纯白": "white", "暖米白": "cream", "浅灰": "light grey",
    "午夜深蓝": "midnight blue", "深绿": "dark green", "深灰": "dark grey",
    "暖焦糖棕": "caramel", "复古卡其": "khaki", "经典大红": "red",
    "明黄": "yellow", "橙红": "orange red", "浅蓝": "light blue",
    "复古多彩": "multicolor",
    # 标题风格
    "秋日温暖": "cozy autumn", "秋日复古": "vintage autumn",
    "秋日童趣": "cute autumn", "简约秋日": "minimal autumn",
    "搞怪万圣": "funny halloween", "趣味万圣": "cute halloween",
    "欢乐万圣": "happy halloween", "暗黑万圣": "dark halloween",
    "圣诞童趣": "cute christmas", "圣诞复古": "vintage christmas",
    "圣诞可爱": "cute christmas", "轻奢圣诞": "elegant christmas",
    "粉萌圣诞": "pink christmas", "简约圣诞": "minimal christmas",
    "经典圣诞": "classic christmas", "复古格纹": "vintage plaid",
    "清新日常": "fresh daily", "温柔居家": "soft home",
}


def build_multi_queries(tags: dict, max_queries: int = 8) -> list:
    """生成**多标签组合**的抓图查询（用户要求：图片要最大程度包含给的标签）。

    为什么需要：以前每个标签单独搜，得到的是"纯南瓜图""纯幽灵图"，
    永远搜不到"南瓜+幽灵+蝙蝠"的综合图。

    优先级：
      1) 全标签组合（主题+元素们+颜色）
      2) 主题 + 元素（两两）
      3) 主题 + 颜色
      4) 单标签（兜底）

    返回的查询按"包含标签数"从多到少排列。
    """
    def dim(names):
        out = []
        for n in names:
            out += list(tags.get(n) or [])
        return out

    th = [TAG_EN_TEXT.get(t, t) for t in dim(("节日主题", "主题"))]
    mo = [TAG_EN_TEXT.get(t, t) for t in dim(("画面元素", "元素"))]
    co = [TAG_EN_TEXT.get(t, t) for t in dim(("配色",))]
    st = [TAG_EN_TEXT.get(t, t) for t in dim(("画风", "风格"))]
    th = [x for x in th if x]
    mo = [x for x in mo if x]
    co = [x for x in co if x]
    st = [x for x in st if x]

    base = th[0] if th else (mo[0] if mo else "pattern")

    out = []          # [(查询, 命中的标签数)]

    def add(words, n):
        q = " ".join(w for w in words if w).strip()
        if q and q not in [x[0] for x in out]:
            out.append((q, n))

    # 1) 全标签组合：主题 + 所有元素 + 颜色  ← 最可能出综合图
    if th and mo:
        add([base] + mo[:3], 1 + min(3, len(mo)))
        add([base] + mo[:3] + co[:1], 1 + min(3, len(mo)) + min(1, len(co)))
    if mo and len(mo) >= 2:
        add(mo[:3], min(3, len(mo)))          # 只要元素们（不带主题）

    # 2) 主题 + 单个元素（两两）
    for m in mo[:3]:
        add([base, m], 2)
        if co:
            add([base, m, co[0]], 3)

    # 3) 主题 + 颜色
    if th and co:
        add([base, co[0]], 2)

    # 4) 主题 + 画风
    if th and st:
        add([base, st[0]], 2)

    # 5) 单标签兜底（放最后）
    if th:
        add([base], 1)
    for m in mo[:2]:
        add([m], 1)

    # 按命中标签数排序（多的在前）
    out.sort(key=lambda x: -x[1])
    return [q for q, _ in out[:max_queries]]
