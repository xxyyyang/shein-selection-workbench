# -*- coding: utf-8 -*-
"""
shein_scout.score —— 打分、硬规则合规检查、逐条可读诊断

输出四类东西：
  preference  0~100 综合分（越高越贴近已过审风格 + 越不容易被刷）
  style_match 0~100 与"已过审画像"的接近程度（数据驱动）
  model_score 0~100 "过审 vs 被刷"判别模型给出的过审概率
  rule_score  0~100 硬规则合规度（尺寸/比例/水印/白底等确定性项）
外加 items[] —— 每一条扣分/加分的原因、实测值、目标区间。
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from . import learn as learn_mod
from . import adjust
from . import rules_by_type
from .learn import NICE, Logit, nice


IMAGETYPE_ZH = {
    "pattern_flat": "图案设计稿",
    "mockup": "商品展示图",
    "photo": "实拍图",
    "unknown": "未识别",
}


def _label(key: str) -> str:
    """有中文名就用中文名，没有就原样返回（别凭空造概念）。"""
    return NICE.get(key, key)

# --------------------------------------------------------------------------
# 默认硬规则（可按自己类目改，或改 config.json）；阈值都写明了依据
# --------------------------------------------------------------------------
DEFAULT_RULES = {
    # SHEIN 商品图建议 ≥1345x1785（3:4）或 ≥900x900，低于此容易被判"图质不达标"
    "min_side": 800,
    "min_pixels": 800 * 800,
    "want_side": 1345,
    "want_pixels": 1345 * 1785,
    # 允许的宽高比（SHEIN 常见 3:4 / 1:1 / 4:5 / 2:3）
    # 2:3(0.667) 是实测到的真实上架比例；9:16 也常见
    "aspect_set": [[0.72, 0.78], [0.98, 1.02], [0.78, 0.82], [0.64, 0.70], [0.55, 0.58]],
    # 白底/浅底 商品图风格
    "white_ratio_min": 0.55,
    "bg_purity_min": 0.45,
    "bg_isolated_white_max": 0.10,
    "corner_bright_min": 0.62,
    "text_suspect_max": 1,          # 1=不允许 文字/水印 嫌疑
    "sharp_min": 0.55,
    "skin_ratio_max": 0.35,
    "clip_white_max": 0.22,
    "subject_area_min": 0.10,
    "subject_area_max": 0.92,
    "subject_centered_min": 0.55,
    "subject_touch_edges_max": 2,
    "color_entropy_max": 0.88,
    "tag": "SHEIN 通用商品图口径（可在 config.json 调整）",
}


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except Exception:
        return d


def _aspect_ok(aspect: float, rule_set) -> bool:
    return any(lo <= aspect <= hi for lo, hi in rule_set)


def check_rules(feat: dict, rules: dict) -> tuple[float, list[dict]]:
    """返回 (rule_score 0~100, items)。每项含 严重度/实测/目标/说明。"""
    items: list[dict] = []
    score = 100.0

    def add(sev: float, key: str, msg: str, got, want: str) -> None:
        nonlocal score
        score -= sev
        items.append({"kind": "rule", "level": "bad" if sev >= 8 else "warn",
                      "feature": key, "name": _label(key),
                      "msg": msg, "got": got, "want": want, "penalty": round(sev, 1)})

    W, H = _f(feat.get("width")), _f(feat.get("height"))
    min_side = min(W, H)
    pixels = _f(feat.get("pixels"))

    if min_side < rules["min_side"]:
        add(26, "resolution", f"分辨率不足：短边仅 {int(min_side)}px，容易被判“图片质量不达标”",
            f"{int(W)}x{int(H)}", f"短边 ≥{rules['min_side']}px")
    elif pixels < rules["want_pixels"] and min_side < rules["want_side"]:
        add(7, "resolution", f"分辨率偏小：{int(W)}x{int(H)}，放大后细节会糊，建议一次出图到 "
                             f"{rules['want_side']}px 以上", f"{int(W)}x{int(H)}",
            f"短边 ≥{rules['want_side']}px")

    aspect = _f(feat.get("aspect"), 1.0)
    if not _aspect_ok(aspect, rules["aspect_set"]):
        add(12, "aspect", f"宽高比 {aspect:.3f} 不是平台常规比例，主图易被裁切或留白不匀",
            round(aspect, 3), "3:4 / 1:1 / 4:5 / 2:3")

    # ---- 文字/水印判定 ----
    # 优先级：OCR 实测 > 图像启发式。
    # 实测教训：启发式在真实照片上误报率极高（24 张真实网图误报 23 张，
    # 麻花针织纹理、深色毛毯、报纸照片都会被判成"文字"），所以启发式只做软警告，
    # 绝不当硬门槛；OCR 能真正读出字，才是可靠判据。
    #
    # OCR 的第二类误报：印花图案内部的小元素会被 OCR 读成零散"字符"
    # （实测已上架印花图读出 "00 艹 00"、"0 0 0"、"0 0 0 0"）。
    # 促销/水印文字是有意义的词组，所以按"字符数 + 是否含中日韩文字或成词"来过滤噪声。
    ocr_chars = _f(feat.get("ocr_chars"))
    ocr_ok = bool(feat.get("ocr_ok"))
    ocr_text = str(feat.get("ocr_text") or "")
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", ocr_text))
    # 去掉空白与孤立标点后的有效字符
    meaningful = re.sub(r"[\s\W_]+", "", ocr_text, flags=re.UNICODE)
    n_meaning = len(meaningful)
    # 噪声特征：全是数字/单个字母的碎片、没有中日韩文字且太短
    looks_like_noise = (n_meaning <= 4 and not has_cjk) or \
                       bool(re.fullmatch(r"[0-9oOoIl\.\-_、，。·\s]+", ocr_text or "x"))
    if ocr_ok:
        # 门槛说明：印花图案会把内部元素读成 4~8 个零散字符（实测已上架印花图
        # 读出「00 艹 00」、「， 区 《 生 0 0」），而真实促销文案至少是
        # 4 个连续汉字（如「双面牛奶绒」）或 8 个以上字符。
        if n_meaning >= 8 or (has_cjk and n_meaning >= 6):
            add(26, "text", f"画面里检测到文字：{ocr_text[:60]}"
                            f"（OCR 识别到 {int(n_meaning)} 个有效字符）。主图叠字/水印是最高频的刷图原因",
                f"{int(n_meaning)} 字符", "0 字符")
        elif n_meaning >= 2 and not looks_like_noise:
            add(8, "text", f"画面里有少量文字：{ocr_text[:40]}"
                           f"（{int(n_meaning)} 个字符），确认是不是必要的品牌/织唛信息",
                f"{int(n_meaning)} 字符", "0 字符")
        elif looks_like_noise and n_meaning >= 2:
            # 印花图案内部元素被误读，不扣分，只留个脚注
            items.append({"kind": "text", "level": "info", "feature": "ocr_noise",
                          "name": "文字识别", "msg": f"OCR 读到零散字符「{ocr_text[:24]}」，"
                          f"按印花噪声处理，未扣分（如需严格可人工确认）",
                          "got": f"{int(n_meaning)} 碎片字符", "want": "—", "penalty": 0.0})
        elif feat.get("text_warn"):
            add(4, "text", "OCR 没读出文字，但画面有较多笔画状纹理（可能是纹理本身），建议扫一眼",
                f"笔画块 {int(_f(feat.get('bh_blobs')))} 处", "无")
    else:
        # 没有 OCR 结果：只给提示，不扣重分
        if _f(feat.get("has_text_suspect")) >= rules["text_suspect_max"]:
            add(12, "text", "疑似画面叠字/水印（未接 OCR，仅凭笔画纹理推断，需人工确认）",
                f"笔画占比 {_f(feat.get('bh_stroke_frac')) * 100:.2f}%", "无文字、无水印")
        elif feat.get("text_warn"):
            add(4, "text", "画面有较多笔画状纹理，可能是文字也可能是布料纹理，建议放大确认",
                f"笔画块 {int(_f(feat.get('bh_blobs')))} 处", "无")

    if _f(feat.get("overlay_concentration")) > 3.0 and _f(feat.get("edge_frame_mean")) > 0.018:
        add(9, "overlay", f"画面四边/四角堆了明显元素（边缘区细节是中心的 "
                          f"{_f(feat.get('overlay_concentration')):.1f} 倍），"
                          f"常见于促销角标、边框、拼接版式",
            f"边框区边缘强度 {_f(feat.get('edge_frame_mean')):.3f}", "边框区干净")

    if _f(feat.get("lap_var_256")) < rules["sharp_min"]:
        add(14, "sharp", "画面发糊：清晰度指标偏低，多因原图被放大或对焦不实",
            round(_f(feat.get("lap_var_256")), 3), f"≥{rules['sharp_min']}")

    if _f(feat.get("clip_white_subject")) > rules["clip_white_max"]:
        add(9, "clip_white", "主体上大面积死白过曝，面料纹理和绒面质感丢失",
            f"{_f(feat.get('clip_white_subject')) * 100:.1f}%（白底背景不计）",
            f"<{rules['clip_white_max'] * 100:.0f}%")

    bg_style = _f(feat.get("white_ratio")) >= 0.5 or _f(feat.get("corner_bright")) >= rules["corner_bright_min"]
    if not bg_style:
        add(10, "white_ratio", "背景不是白底/浅底：SHEIN 主图偏好干净浅底，场景图一般只放副图",
            f"白底 {_f(feat.get('white_ratio')) * 100:.0f}%，四角亮度 {_f(feat.get('corner_bright')):.2f}",
            f"白底 ≥{rules['white_ratio_min'] * 100:.0f}% 或四角亮度 ≥{rules['corner_bright_min']}")

    if _f(feat.get("bg_isolated_white")) > rules["bg_isolated_white_max"]:
        add(8, "bg_isolated_white", "背景里存在成片孤立白块（常见于抠图残留或补白色块），修图痕迹明显",
            f"{_f(feat.get('bg_isolated_white')) * 100:.1f}%", f"<{rules['bg_isolated_white_max'] * 100:.0f}%")

    sa = _f(feat.get("subject_area"))
    if sa < rules["subject_area_min"]:
        add(13, "subject_area", "主体太小：画面空得厉害，缩略图里看不清产品",
            f"{sa * 100:.1f}%", f"≥{rules['subject_area_min'] * 100:.0f}%")
    elif sa > rules["subject_area_max"]:
        add(8, "subject_area", "主体顶满画幅：几乎没有留白，观感压抑且不符合平台版式",
            f"{sa * 100:.1f}%", f"≤{rules['subject_area_max'] * 100:.0f}%")

    if _f(feat.get("subject_centered")) < rules["subject_centered_min"]:
        add(8, "subject_centered", "主体明显偏心，视觉重心不稳",
            round(_f(feat.get("subject_centered")), 2), f"≥{rules['subject_centered_min']}")

    if int(_f(feat.get("subject_touch_edges"))) > rules["subject_touch_edges_max"]:
        add(9, "subject_touch_edges", f"主体顶到 {int(_f(feat.get('subject_touch_edges')))} 条画面边缘，"
                                      f"平台多角度裁切时会被切掉产品", int(_f(feat.get("subject_touch_edges"))), "≤2")

    if _f(feat.get("skin_ratio")) > rules["skin_ratio_max"]:
        add(7, "skin_ratio", "画面里人物/手部占比偏高，主图容易被判主体不明确",
            f"{_f(feat.get('skin_ratio')) * 100:.0f}%", f"≤{rules['skin_ratio_max'] * 100:.0f}%")

    if _f(feat.get("color_entropy")) > rules["color_entropy_max"]:
        add(6, "color_entropy", "配色杂乱：颜色种类过多，缺乏统一色调",
            round(_f(feat.get("color_entropy")), 3), f"≤{rules['color_entropy_max']}")

    if feat.get("has_alpha"):
        add(4, "alpha", "原图带透明通道，平台转换后可能出现黑边/白边", "含 alpha", "不透明底")

    return max(0.0, min(100.0, score)), items


def style_match(feat: dict, profile: dict) -> tuple[float, list[dict]]:
    """与已过审画像的接近程度。返回 (0~100, 偏离明细)。"""
    stats = profile.get("stats") or {}
    if not stats:
        return 0.0, []
    devs = []
    for key, s in stats.items():
        if key not in feat:
            continue
        z = (_f(feat.get(key)) - _f(s.get("median"))) / max(1e-6, _f(s.get("scale"), 1.0))
        devs.append((key, z, s))
    if not devs:
        return 0.0, []
    zs = [min(abs(z), 3.0) for _, z, _ in devs]
    d = math.sqrt(sum(z * z for z in zs) / len(zs))       # 平均偏离（z 单位）
    score = 100.0 * math.exp(-d / 1.25)
    devs.sort(key=lambda t: -abs(t[1]))
    items = []
    for key, z, s in devs[:5]:
        if abs(z) < 1.0:
            continue
        med, scl = _f(s.get("median")), _f(s.get("scale"), 1.0)
        lo, hi = med - scl, med + scl
        items.append({"kind": "style", "level": "info" if abs(z) < 1.6 else "warn",
                      "feature": key, "name": nice(key),
                      "msg": f"{nice(key)} 与过审图风格不一致（{abs(z):.1f}σ 偏离）",
                      "got": round(_f(feat.get(key)), 4),
                      "want": f"{lo:.3f} ~ {hi:.3f}",
                      "dir": "偏低" if z < 0 else "偏高"})
    if d < 0.5:
        items.insert(0, {"kind": "style", "level": "good", "feature": "style",
                         "msg": "整体影调与版式非常贴近你已过审的那批图", "got": round(d, 3), "want": "<0.5"})
    return score, items


def verdict_of(score: float) -> tuple[str, str]:
    if score >= 80:
        return "推荐", "good"
    if score >= 66:
        return "可上架", "ok"
    if score >= 52:
        return "需修改", "warn"
    return "不建议", "bad"


def score_one(feat: dict, profile: dict, logit: Logit | None, rel: float,
              rules: dict, w_style: float = 0.55, w_model: float = 0.30, w_rule: float = 0.15,
              style_rel: float | None = None, itype: dict | None = None) -> dict:
    # 按图型分派判据：印花图案稿与商品展示图的合格标准完全不同。
    # 实测教训：用同一套通用规则评印花毯图，8 张已上架图平均被扣 28.8 分。
    img_type = (itype or {}).get("type") or "unknown"
    if img_type == "pattern_flat":
        rule_score, rule_items = rules_by_type.check_pattern_quality(feat, itype)
    elif img_type == "mockup":
        rule_score, rule_items = rules_by_type.check_mockup(feat, rules.get("mockup"))
    else:
        rule_score, rule_items = check_rules(feat, rules)
    st_score, st_items = style_match(feat, profile) if profile.get("stats") else (0.0, [])
    mdl = float(logit.proba([feat])[0]) * 100.0 if (logit is not None and logit.w is not None) else None

    has_style = bool(profile.get("stats"))
    has_model = mdl is not None and rel > 0.03
    if style_rel is None:
        from .learn import profile_reliability
        style_rel = profile_reliability(profile)[0] if has_style else 0.0

    used = {}
    total = 0.0
    if has_style:
        # 画像可信度低时按比例降权，避免小样本画像一票否决
        used["style"] = max(0.05, w_style * style_rel)
    if has_model:
        used["model"] = w_model * rel
    used["rule"] = w_rule
    s = sum(used.values()) or 1.0
    for k in used:
        used[k] /= s

    pref = 0.0
    if has_style:
        pref += used["style"] * st_score
    if has_model:
        pref += used["model"] * mdl
    pref += used["rule"] * rule_score

    # 硬伤封顶：规则层面严重违规时，风格再像也不能算好图
    caps = ((rule_score < 45, 48), (rule_score < 60, 60), (rule_score < 75, 78))
    for cond, cap in caps:
        if cond:
            pref = min(pref, float(cap))
            break

    # 只有硬规则可用时（没有画像、没有模型），分数必须封顶：
    # 硬规则只能回答"有没有踩红线"，回答不了"符不符合平台审美"。
    # 实测教训：纯规则下，非白底的生活场景图能拿到 93 分被判"推荐" —— 明显虚高。
    note = ""
    if (itype or {}).get("_ood_switched"):
        note = "本次用色彩口径模型评分（这批图与训练数据来源不同）"
    if not has_style and not has_model:
        pref = min(pref, 74.0)
        note = "仅硬规则审计：还没有过审画像，无法判断是否贴合平台审美（分数已封顶 74）"
    elif not has_style:
        note = "无过审画像，风格分为空，仅靠硬规则与判别模型"
    pref = max(0.0, min(100.0, pref))

    # 调色建议：实测成功上架的图饱和度显著更低（sat_mean 0.198 vs 被刷 0.327），
    # 所以这里不只报"饱和度偏高"，而是给出可执行的降幅 + 预览图。
    try:
        adj = adjust.plan(feat, profile)
    except Exception:
        adj = {"needed": False, "factors": [], "reasons": []}
    if adj.get("needed"):
        # 每个指标各带自己的实测值与阈值，避免三行都显示同一个数字
        proj = adj.get("projection") or {}
        META = {"平均饱和度": ("sat_mean", "饱和度"),
                "色彩鲜艳度": ("colorfulness", "鲜艳度"),
                "高饱和区域占比": ("sat_p90", "高饱和占比")}
        for rf in adj.get("reasons", []):
            key, label = None, None
            for zh, (k, lb) in META.items():
                if rf.startswith(zh):
                    key, label = k, lb
                    break
            got, want = f"当前 {adj.get('current')}", f"目标 ≈{adj.get('target')}"
            if key and proj.get(key):
                got = f"{label} {proj[key]['now']}"
                want = f"≤{proj[key]['threshold']}"
            rule_items.append({"kind": "color", "level": "warn",
                               "feature": "saturation", "name": "色彩饱和度",
                               "msg": rf, "got": got, "want": want, "penalty": 0.0})

    v, vcls = verdict_of(pref)
    reasons = [i for i in rule_items if i["level"] in ("bad", "warn")] + \
              [i for i in st_items if i["level"] in ("warn", "info")]
    good = [i for i in st_items if i["level"] == "good"]
    return {
        "preference": round(pref, 1),
        "style_match": round(st_score, 1) if has_style else None,
        "model_score": None if mdl is None else round(mdl, 1),
        "model_reliability": round(rel, 3),
        "rule_score": round(rule_score, 1),
        "verdict": v, "verdict_class": vcls,
        "adjust": adj,
        "img_type": img_type,
        "img_type_zh": IMAGETYPE_ZH.get(img_type, img_type),
        "img_type_confidence": (itype or {}).get("confidence"),
        "img_type_evidence": (itype or {}).get("evidence", [])[:3],
        "note": note,
        "weights_used": {k: round(x, 3) for k, x in used.items()},
        "reasons": reasons, "highlights": good,
    }


# --------------------------------------------------------------------------
# 批量打分
# --------------------------------------------------------------------------
def _load_full_model(root: str | Path | None = None):
    """载入完整模型（26 特征，含构图类）。现在只用于 OOD 检测，不再参与打分。"""
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    fp = base / "work" / "model.json"
    if not fp.exists():
        return None
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        return Logit.from_json(obj["logit"])
    except Exception:
        return None


def _load_color_model(root: str | Path | None = None):
    """载入色彩口径模型（只用 9 个可迁移的色彩/笔画特征）。

    这现在是**默认模型** —— 实测在自家数据与网图上都优于 26 特征的完整模型：
      自家(留一文件夹) 76.7% vs 71.0%；网图饱和度相关性 -0.701 vs -0.171。
    """
    base = Path(root) if root else Path(__file__).resolve().parent.parent
    fp = base / "work" / "color_model.json"
    if not fp.exists():
        return None
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        return Logit.from_json(obj["logit"])
    except Exception:
        return None


def detect_ood(probs: list[float], pos_median: float = 0.85) -> dict:
    """检测一批图的模型概率是否落在训练分布之外。

    判据：模型在训练分布内应给出有层次的概率；若大量样本挤在两端（<0.15 或 >0.85），
    说明输入与训练分布不符 —— 此时模型在区分"像不像训练集"，而不是"风格好不好"。

    实测：37 张网图里只有 14% 落在中间区间时，模型分与饱和度的相关性从训练时的
    强相关掉到 -0.171，属不可靠状态。
    """
    if not probs:
        return {"ood": False, "mid_ratio": 0.0, "reason": "无样本"}
    n = len(probs)
    mid = sum(1 for x in probs if 0.15 < x < 0.85) / n
    lo = sum(1 for x in probs if x <= 0.15) / n
    hi = sum(1 for x in probs if x >= 0.85) / n
    ood = mid < 0.25 and n >= 8
    reason = ""
    if ood:
        reason = (f"本批 {n} 张里只有 {mid:.0%} 落在模型的中间判断区间"
                  f"（{lo:.0%} 被推向'很成功'、{hi:.0%} 被推向'很被刷'），"
                  f"说明这些图的来源与训练数据不同")
    return {"ood": ood, "mid_ratio": round(mid, 3),
            "lo_ratio": round(lo, 3), "hi_ratio": round(hi, 3), "reason": reason}


_imagetype_cache: dict = {}


def score_rows(rows: list[dict], profile: dict, logit: Logit | None,
               rules: dict | None = None, train_files: set[str] | None = None,
               force_type: str = "auto") -> list[dict]:
    rules = rules or DEFAULT_RULES
    train_files = train_files or set()
    rel = 0.0
    style_rel = 0.0
    if logit is not None:
        n_pos = int(profile.get("n_approved", 0) or 0)
        n_neg = int(profile.get("n_rejected", 0) or 0)
        from .learn import model_reliability
        rel, _ = model_reliability(logit, n_pos, n_neg)
    if profile.get("stats"):
        style_rel, _ = learn_mod.profile_reliability(profile)
    # 先批量算一遍模型概率，用于 OOD 检测
    # OOD 检测用**完整模型**算：它的特征含构图类，更能反映"像不像训练分布"。
    # 色彩模型只看色彩，天然对来源不敏感，不适合做 OOD 判据。
    ood_info = {"ood": False, "mid_ratio": 0.0, "reason": ""}
    _full = _load_full_model()
    _probe = _full if (_full is not None and _full.w is not None) else logit
    if _probe is not None and _probe.w is not None:
        _ok_rows = [r for r in rows if r.get("ok")]
        if _ok_rows:
            try:
                _p = _probe.proba(_ok_rows)
                ood_info = detect_ood([float(x) for x in _p])
            except Exception:
                pass
    # 统一使用色彩口径模型（实测在各场景都优于完整模型，见 _load_color_model 注释）。
    # 仍保留 OOD 检测，但只用于**提示**（告知这批图与训练数据来源不同），
    # 不再改变模型选择 —— 避免依赖一个脆弱阈值导致行为随批次漂移。
    _cm = _load_color_model()
    if _cm is not None and _cm.w is not None:
        # OOD 检测仍在旧模型上算（它更能反映"像不像训练分布"）
        logit = _cm
        rel = max(rel, 0.75)
        ood_info["switched_to"] = "color_model_default"
    else:
        # 没有色彩模型时退回完整模型
        ood_info["switched_to"] = "full_model_fallback"

    out = []
    for r in rows:
        if not r.get("ok"):
            out.append({**r, "preference": 0.0, "verdict": "读图失败", "verdict_class": "bad",
                        "reasons": [{"kind": "io", "level": "bad",
                                     "msg": r.get("error") or "无法读取", "got": "", "want": ""}]})
            continue
        # 图型识别（图案稿 vs 展示图），结果按文件缓存，避免重复计算周期相关性
        itype = _imagetype_cache.get(r.get("file", ""))
        if itype is None:
            try:
                from . import imagetype as _it
                itype = _it.classify(r.get("file", ""), r)
            except Exception as e:
                itype = {"type": "unknown", "confidence": 0.0,
                         "evidence": [f"识别失败: {type(e).__name__}"], "metrics": {}}
            _imagetype_cache[r.get("file", "")] = itype
        if force_type in ("product", "design", "candidate"):
            itype = dict(itype or {})
            auto_type = itype.get("type")
            if force_type == "design":
                itype["type"] = "pattern_flat"
            else:
                # candidate / product 都按产品展示图评。
                # candidate 额外记录"自动分型认为它是设计稿"这件事，供报告标注，
                # 因为筛网图时经常混进平铺图案文件，用户需要知道哪些是设计稿。
                itype["type"] = "mockup"
                if force_type == "candidate":
                    itype["auto_type"] = auto_type
                    itype["suspect_design"] = (auto_type == "pattern_flat")
            itype["forced"] = force_type
        s = score_one(r, profile, logit, rel, rules, style_rel=style_rel, itype=itype)
        s["ood"] = ood_info
        # 记录本行用的是哪个模型 —— 分布外会切换成色彩口径模型，必须可追溯
        sw = ood_info.get("switched_to")
        if sw == "full_model_fallback":
            s["model_used"] = "完整模型(26特征·回退)"
        elif logit is None or logit.w is None:
            s["model_used"] = "无模型"
        else:
            s["model_used"] = "色彩口径模型(9特征)"
        s["ood_flag"] = bool(ood_info.get("ood"))
        # 数据泄漏标记：这张图参与过训练/建画像，它的分数天生偏高，不能当验证依据
        is_train = str(r.get("file", "")).lower() in train_files
        if is_train:
            s["used_in_training"] = True
            s["note"] = (s.get("note") + " ｜ " if s.get("note") else "") + \
                "⚠ 此图参与过画像/模型训练，分数偏乐观，不能用来判断模型准不准"
            s["reasons"] = [{"kind": "leak", "level": "warn",
                             "msg": "该图是训练样本之一，分数仅供自检，不构成验证",
                             "got": "", "want": ""}] + list(s.get("reasons") or [])
        else:
            s["used_in_training"] = False
        out.append({**r, **s})
    return out
