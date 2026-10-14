# -*- coding: utf-8 -*-
"""
shein_scout.rules_by_type —— 按图型分开的判据

为什么必须分型
--------------
实测用户 8 张已上架图，通用规则平均扣 28.8 分、4 张被判"需修改" —— 全是误判。
根因：规则默认"图里有个中心商品主体"，但**印花图案平铺文件根本没有中心主体**：
  * 主体占比 0.7%~5.8%  -> 被扣"主体太小"
  * 配色熵天然偏高      -> 被扣"配色杂乱"
  * 图案铺满整幅        -> 被扣"非白底"
把产品特性当成了缺陷。

分型后的判据
------------
A. pattern_flat（印花图案稿）：看**设计质量**，不看商品摄影规范
   - 有效调色板规模（太少=单调，太多=杂乱）
   - 主色占比（是否有一眼能认出的主色）
   - 纯黑/纯白占比（大面积死黑死白会显得廉价）
   - 明度跨度与对比
   - 构图：元素分布是否有留白呼吸感
   - 周期性（无缝稿该能拼接）

B. mockup（白底商品展示图）：看**平台审美与合规**（用户的驳回理由是"风格不符"）
   - 背景是否为干净白底/浅底（含灰阶与色温一致性的近似判断）
   - 商品占比与留白比例、是否居中
   - 是否顶边（多角度裁切会切到产品）
   - 是否有真人模特及其占比
   - 清晰度、过曝、文字

注意：这里的"审美"阈值仍是通用经验值，**必须用你真实的过审/被刷图校准**。
"""
from __future__ import annotations

import math


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except Exception:
        return d


def _item(level, kind, msg, got, want, penalty=0.0) -> dict:
    return {"kind": kind, "level": level, "msg": msg, "got": got, "want": want,
            "penalty": round(penalty, 1)}


# --------------------------------------------------------------------------
# A. 印花图案稿：设计质量判据
# --------------------------------------------------------------------------
def check_pattern_quality(feat: dict, itype: dict | None = None) -> tuple[float, list[dict]]:
    """返回 (设计质量分 0~100, items)。分数衡量"这张图案稿本身作为设计素材的质量"。"""
    items: list[dict] = []
    s = 100.0

    pal = int(_f((itype or {}).get("metrics", {}).get("palette")))
    if pal == 0:
        pal = int(_f(feat.get("unique_colors8")) * 40)
    entropy = _f(feat.get("color_entropy"))
    dom3 = _f(feat.get("dom3_cov"))
    dark = _f(feat.get("clip_black"))
    bright = _f(feat.get("clip_white"))
    contrast = _f(feat.get("contrast_rms"))
    L = _f(feat.get("L_mean"), 0.5)
    edge = _f(feat.get("edge_density"))
    fp_std = _f(feat.get("bright_grid_std"))

    # 1) 主色是否明确：主色占比太低 = 画面没有视觉重心
    if dom3 < 0.18:
        s -= 12
        items.append(_item("warn", "pattern_theme",
                           "图案缺乏主导色：前三大色块合计占比过低，整体显得碎",
                           f"前三主色合计 {dom3:.0%}", "≥30%", 12))
    elif dom3 > 0.92:
        s -= 6
        items.append(_item("info", "pattern_theme",
                           "配色高度集中，风格统一（注意是否过于单调）",
                           f"前三主色合计 {dom3:.0%}", "30%~92%", 6))

    # 2) 配色复杂度
    if entropy > 0.75:
        s -= 10
        items.append(_item("warn", "pattern_palette",
                           "配色偏杂：颜色种类过多，上身后容易显得乱",
                           f"配色熵 {entropy:.2f}", "≤0.75", 10))
    elif entropy < 0.12:
        s -= 6
        items.append(_item("info", "pattern_palette",
                           "配色非常单一，可能缺少层次",
                           f"配色熵 {entropy:.2f}", "0.12~0.75", 6))

    # 3) 死黑/死白：印花大面积纯黑或纯白在实物上会显廉价、且印刷易糊
    if dark > 0.30:
        s -= 12
        items.append(_item("warn", "pattern_flat_color",
                           "大面积纯黑（印花后容易显死板，且黑色区域细节会丢失）",
                           f"纯黑占 {dark:.0%}", "<30%", 12))
    if bright > 0.45:
        s -= 10
        items.append(_item("warn", "pattern_flat_color",
                           "大面积纯白（印花到绒面料上白色会偏灰，实际效果与设计稿差别大）",
                           f"纯白占 {bright:.0%}", "<45%", 10))

    # 4) 明度层次
    if L < 0.28 or L > 0.88:
        s -= 8
        items.append(_item("info", "pattern_tone",
                           "整体明度偏" + ("暗" if L < 0.28 else "亮") + "，注意成品观感",
                           f"感知明度 {L:.2f}", "0.28~0.88", 8))
    if contrast < 0.10:
        s -= 8
        items.append(_item("warn", "pattern_contrast",
                           "对比度偏低：图案元素与底色太接近，缩略图上看不清",
                           f"对比度 {contrast:.3f}", "≥0.10", 8))

    # 5) 构图呼吸感：元素过密或过疏
    if edge > 0.45:
        s -= 6
        items.append(_item("info", "pattern_density",
                           "元素非常密集，缩略图下会糊成一片",
                           f"细节密度 {edge:.0%}", "<45%", 6))
    if fp_std < 0.04:
        s -= 6
        items.append(_item("info", "pattern_balance",
                           "各区域明暗几乎一致，画面缺少疏密变化",
                           f"分区明度标准差 {fp_std:.3f}", "≥0.04", 6))

    s = max(0.0, min(100.0, s))
    return s, items


# --------------------------------------------------------------------------
# B. 商品展示图（mockup）：平台审美与合规
# --------------------------------------------------------------------------
MOCKUP_RULES = {
    # 分辨率：按用户实际工作流校准。其 PS 模板标准输出就是 1000x1004，
    # 而实测成功上架组的短边中位数正是 1000 —— 所以 1000 必须算合格。
    # 早期把"理想短边"设成 1345 导致每张图白扣 6~24 分（含成功上架的图）。
    "min_side": 800,          # 低于此判"图质不达标"（599 那张会命中）
    "want_side": 1000,        # 达到 1000 就不扣分
    "aspect_set": [[0.72, 0.78], [0.98, 1.02], [0.78, 0.82], [0.64, 0.70], [0.55, 0.58]],
    # 商品主体占比：实测成功组中位 0.83
    "subject_area_min": 0.20,
    "subject_area_max": 0.95,
    "subject_centered_min": 0.55,
    "subject_touch_edges_max": 2,
    # 背景：以"四角亮度"为主判据。
    # 实测：商品占满画幅时纯白占比天然很低（成功图 4.png 仅 14.7%，但四角是纯白 1.0），
    # 所以纯白占比只做辅助，不能当硬门槛。
    "corner_bright_min": 0.60,
    "white_ratio_min": 0.12,
    "corner_consistency_min": 0.80,
    "sharp_min": 0.55,
    "clip_white_max": 0.35,
    "skin_ratio_max": 0.35,
}


def check_mockup(feat: dict, mr: dict | None = None) -> tuple[float, list[dict]]:
    """返回 (展示图合规审美分 0~100, items)。"""
    r = dict(MOCKUP_RULES)
    r.update(mr or {})
    items: list[dict] = []
    s = 100.0

    def pen(sev, kind, msg, got, want):
        nonlocal s
        s -= sev
        items.append(_item("bad" if sev >= 10 else "warn", kind, msg, got, want, sev))

    W, H = _f(feat.get("width")), _f(feat.get("height"))
    min_side = min(W, H)
    if min_side < r["min_side"]:
        pen(24, "resolution", f"分辨率不足：短边 {int(min_side)}px", f"{int(W)}x{int(H)}",
            f"短边 ≥{r['min_side']}px")
    elif min_side < r["want_side"]:
        pen(6, "resolution", f"分辨率偏小：{int(W)}x{int(H)}，建议出到 {r['want_side']}px 以上",
            f"{int(W)}x{int(H)}", f"短边 ≥{r['want_side']}px")

    aspect = _f(feat.get("aspect"), 1.0)
    if not any(lo <= aspect <= hi for lo, hi in r["aspect_set"]):
        pen(10, "aspect", f"宽高比 {aspect:.3f} 不是平台常规比例（同一 SPU 内比例要统一）",
            round(aspect, 3), "3:4 / 1:1 / 4:5 / 2:3")

    # 背景干净度 —— "不符合平台审美"里最常见的具体表现就是背景不干净
    white = _f(feat.get("white_ratio"))
    corner = _f(feat.get("corner_bright"))
    consist = _f(feat.get("corner_consistency"))
    if white < 0.20 and corner < r["corner_bright_min"]:
        pen(20, "background",
            "背景既不是白底也不是浅底，作为主图容易被判风格不符",
            f"白底 {white:.0%}、四角亮度 {corner:.2f}",
            f"白底 ≥{r['white_ratio_min']:.0%} 或四角亮度 ≥{r['corner_bright_min']}")
    elif 0.20 <= white < r["white_ratio_min"]:
        pen(9, "background",
            "背景白得不彻底（有灰阶或色偏），平台对主图白底要求干净",
            f"白底 {white:.0%}", f"≥{r['white_ratio_min']:.0%}")
    if corner >= 0.6 and consist < r["corner_consistency_min"]:
        # 门控：四角里有主体内容时（比如模特头发、披挂的毯子角）不一致是正常的，
        # 不能当"版式脏"。实测教训：模特举毯图四角一致性 0.00，但四角是头发和白底，
        # 属于正常构图，误报了一条"四角底色不一致"。
        frame_frac = _f(feat.get("subject_touch_edges"))
        if frame_frac < 2:
            pen(8, "background_uniform",
                "四角底色不一致（明暗或色温不统一），版式会显得脏",
                f"四角一致性 {consist:.2f}", f"≥{r['corner_consistency_min']}")

    # 透明背景风险：PS 模板导出的 PNG 常带 alpha 通道，上传后可能被填成黑边
    if feat.get("has_alpha"):
        # 有 alpha 且主体触及边缘时风险更高：抠图没铺满底
        sev = 14 if _f(feat.get("subject_touch_edges")) >= 1 else 8
        pen(sev, "alpha",
            "这张图带透明通道（PS 模板直接导出常见）。平台转 JPG 时透明区会被填成黑边或白边，"
            "建议导出前在 PS 里拼合图像，或垫一层白底再存 JPG",
            "含 alpha 通道", "不透明底（白底铺满）")

    # 主体是否被画幅切掉：模板里毯子出框是最常见的硬伤
    sides = []
    for k, zh in (("subject_margin_top", "上"), ("subject_margin_bottom", "下"),
                  ("subject_margin_left", "左"), ("subject_margin_right", "右")):
        if _f(feat.get(k), 1.0) <= 0.005:
            sides.append(zh)
    if sides:
        pen(10, "clipped",
            f"毯子被画幅切掉（{'、'.join(sides)}边贴边）。平台多角度展示会裁切，容易把产品切残",
            f"{'、'.join(sides)}边留白 0", "四边留白 ≥1%")

    sa = _f(feat.get("subject_area"))
    if sa < r["subject_area_min"]:
        pen(12, "subject_area", "商品在画面里太小，缩略图看不清毯面图案",
            f"商品占比 {sa:.0%}", f"≥{r['subject_area_min']:.0%}")
    elif sa > r["subject_area_max"]:
        pen(7, "subject_area", "商品顶满画幅，几乎没有留白",
            f"商品占比 {sa:.0%}", f"≤{r['subject_area_max']:.0%}")
    if _f(feat.get("subject_centered")) < r["subject_centered_min"]:
        pen(8, "subject_centered", "商品偏心，视觉重心不稳",
            round(_f(feat.get("subject_centered")), 2), f"≥{r['subject_centered_min']}")
    if int(_f(feat.get("subject_touch_edges"))) > r["subject_touch_edges_max"]:
        pen(8, "subject_touch_edges",
            f"商品顶到 {int(_f(feat.get('subject_touch_edges')))} 条画面边缘，多角度裁切会切到产品",
            int(_f(feat.get("subject_touch_edges"))), f"≤{r['subject_touch_edges_max']}")

    # 留白比例（审美项）：主图讲究留白均匀
    mts = [_f(feat.get(k)) for k in ("subject_margin_top", "subject_margin_bottom",
                                     "subject_margin_left", "subject_margin_right")]
    if any(m > 0 for m in mts):
        spread = max(mts) - min(mts)
        if spread > 0.28:
            pen(6, "margin", "四周留白不均匀，版式显得随意",
                f"留白差 {spread:.2f}", "≤0.28")

    if _f(feat.get("lap_var_256")) < r["sharp_min"]:
        pen(12, "sharp", "画面发糊：清晰度不足", round(_f(feat.get("lap_var_256")), 2),
            f"≥{r['sharp_min']}")
    if _f(feat.get("clip_white_subject")) > r["clip_white_max"]:
        pen(8, "clip_white", "毯面大面积死白，图案纹理丢失",
            f"{_f(feat.get('clip_white_subject')) * 100:.0f}%",
            f"<{r['clip_white_max'] * 100:.0f}%")
    skin = _f(feat.get("skin_ratio"))
    if skin > r["skin_ratio_max"]:
        pen(6, "skin_ratio", "真人占比偏高，商品主体不够突出", f"{skin:.0%}",
            f"≤{r['skin_ratio_max']:.0%}")

    return max(0.0, min(100.0, s)), items
