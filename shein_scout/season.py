# -*- coding: utf-8 -*-
"""
档期 × 选品 联动引擎

设计要点（按用户反馈修正）：
  * **市场是数据属性，不是视图开关** —— 一次同时看所有市场
  * **市场列表开放** —— 加市场只改配置，不改代码
  * **同一节日可跨市场，但各有各的标签推荐**
    （圣诞节在 US/GB 是红绿金，在 SA 完全不同）
  * 档期 -> 标签 -> 英文关键词 -> 抓图，一条链

配置存 档期配置.json，首次运行自动生成模板。
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import holidays as hd

CFG = Path.home() / "Desktop" / "档期配置.json"

# ============================================================ 静态配置（可被 json 覆盖）
# 市场注册表：加市场在这里加一行即可
MARKETS = {
    "SA": {"中文名": "沙特", "日历代码": "SA", "语言": "en",
           "购物节": ["白色星期五", "返校季"], "电商主节点": "斋月"},
    "GB": {"中文名": "英国", "日历代码": "GB", "语言": "en",
           "购物节": ["黑五", "节礼日", "返校季"], "电商主节点": "圣诞节"},
    "US": {"中文名": "美国", "日历代码": "US", "语言": "en",
           "购物节": ["黑五", "网购星期一", "返校季", "独立日"],
           "电商主节点": "圣诞节"},
}

# 标签 -> 抓图关键词
TAG_KEYWORDS = {
    "万圣节": ["halloween pattern", "pumpkin ghost print", "spooky seamless"],
    "秋季": ["fall leaves pattern", "autumn maple print", "harvest fabric"],
    "感恩节": ["thanksgiving pattern", "turkey harvest print"],
    "圣诞节": ["christmas pattern", "santa snowflake print", "xmas seamless"],
    "情人节": ["valentine heart pattern", "love hearts print"],
    "复活节": ["easter bunny pattern", "spring egg print"],
    "母亲节": ["mothers day pattern", "floral mom print"],
    "父亲节": ["fathers day pattern", "dad gift print"],
    "独立日": ["4th july pattern", "patriotic stars stripes"],
    "新年": ["new year pattern", "fireworks celebration print"],
    "斋月": ["ramadan pattern", "mosque lantern print",
             "ramadan kareem seamless", "crescent moon star pattern",
             "arabesque islamic print"],
    "开斋节": ["eid mubarak pattern", "eid al fitr print",
               "eid celebration seamless"],
    "宰牲节": ["eid al adha pattern", "sacrifice festival print"],
    "白色星期五": ["cozy winter pattern", "warm throw fabric"],
    "建国日": ["saudi founding day pattern", "saudi heritage print",
               "arabic calligraphy pattern"],
    "国庆日": ["saudi national day pattern", "saudi green print"],
    "返校季": ["back to school pattern", "school supplies print"],
    "动物": ["animal pattern", "cute pet print", "woodland animal seamless"],
    "宠物": ["pet pattern", "dog cat print", "paw print seamless"],
    "花卉": ["floral pattern", "botanical print", "flower seamless"],
    "几何": ["geometric pattern", "abstract shapes print"],
    "格纹": ["plaid pattern", "checkered print", "tartan seamless"],
    "卡通": ["cartoon pattern", "kawaii print"],
    "文字标语": ["typography pattern", "quote print"],
    "星空": ["celestial pattern", "moon star print"],
    "海洋": ["ocean pattern", "nautical print"],
    "暖橙红": ["warm orange pattern", "rust red print"],
    "大地色": ["earth tone pattern", "terracotta print"],
    "黑白灰": ["black white pattern", "monochrome print"],
    "莫兰迪": ["muted pastel pattern", "morandi tone print"],
    "马卡龙粉": ["pastel pink pattern", "macaron color print"],
    "蓝绿冷": ["teal blue pattern", "cool green print"],
    "金棕": ["gold brown pattern", "amber print"],
    "绿白": ["green white pattern", "emerald print"],
    "红金": ["red gold pattern", "festive red print"],
    "手绘插画": ["hand drawn pattern", "illustration fabric print"],
    "水彩": ["watercolor pattern", "painterly print"],
    "卡通可爱": ["cute cartoon pattern", "kawaii seamless"],
    "扁平矢量": ["flat vector pattern", "minimal graphic print"],
    "写实": ["realistic pattern", "photographic print"],
    "复古": ["vintage pattern", "retro print"],
    "版画": ["block print pattern", "woodcut print"],
    "线稿": ["line art pattern", "outline drawing print"],
    "文字": ["text pattern", "word print"],
    "边框": ["border pattern", "frame print"],
    "格纹底": ["plaid background", "checked base pattern"],
    "星星": ["star pattern", "celestial stars print"],
    "月亮": ["moon pattern", "crescent moon print"],
    "灯笼": ["lantern pattern", "ramadan lantern print"],
    "清真寺": ["mosque pattern", "islamic architecture print"],
    "雪花": ["snowflake pattern", "winter snow print"],
    "枫叶": ["maple leaf pattern", "autumn leaf print"],
    "南瓜": ["pumpkin pattern", "pumpkin patch print"],
    "幽灵": ["ghost pattern", "spooky ghost print"],
    "驯鹿": ["reindeer pattern", "christmas deer print"],
    "爱心": ["heart pattern", "love heart print"],
    "阿拉伯纹样": ["arabesque pattern", "islamic geometric print"],
}

# 档期 -> 标签推荐。结构：{市场: {节日: {主题,配色,风格,元素,备注}}}
# 同一节日出现在多个市场时，各自独立配置（审美不同）
EVENT_TAGS = {
    "SA": {
        "斋月": {"主题": ["斋月"], "配色": ["金棕", "绿白", "蓝绿冷"],
                 "风格": ["手绘插画", "扁平矢量", "阿拉伯纹样"],
                 "元素": ["灯笼", "月亮", "星星", "清真寺"],
                 "备注": "全年最大消费季，提前 2~3 个月备货。家居装饰热销"},
        "开斋节": {"主题": ["开斋节"], "配色": ["金棕", "绿白", "马卡龙粉"],
                   "风格": ["手绘插画", "卡通可爱"],
                   "元素": ["月亮", "星星", "灯笼"],
                   "备注": "送礼高峰，礼盒感重要，假期 3~4 天"},
        "宰牲节": {"主题": ["宰牲节"], "配色": ["大地色", "绿白"],
                   "风格": ["扁平矢量"], "元素": ["月亮", "清真寺"],
                   "备注": "家庭聚会为主，家居用品需求上升"},
        "国庆日": {"主题": ["国庆日"], "配色": ["绿白"], "风格": ["扁平矢量"],
                   "元素": ["星星"], "备注": "国旗绿+白，9 月下旬"},
        "建国日": {"主题": ["建国日"], "配色": ["大地色", "绿白"],
                   "风格": ["复古", "手绘插画"], "元素": ["阿拉伯纹样"],
                   "备注": "2022 年新设，传统文化元素（阿拉伯纹样、骆驼、剑）"},
        "白色星期五": {"主题": ["白色星期五"], "配色": ["暖橙红", "大地色"],
                       "风格": ["手绘插画"], "元素": ["雪花"],
                       "备注": "等于黑五，11 月下旬。折扣驱动"},
        "返校季": {"主题": ["返校季"], "配色": ["蓝绿冷", "马卡龙粉"],
                   "风格": ["卡通可爱"], "元素": ["文字"], "备注": "8 月下旬开学"},
        "新年": {"主题": ["新年"], "配色": ["红金", "黑白灰"],
                 "风格": ["扁平矢量"], "元素": ["星星"], "备注": ""},
    },
    "GB": {
        "万圣节": {"主题": ["万圣节"], "配色": ["暖橙红", "黑白灰"],
                   "风格": ["手绘插画", "卡通可爱"], "元素": ["南瓜", "幽灵"],
                   "备注": "热度低于美国。10 月下旬"},
        "圣诞节": {"主题": ["圣诞节"], "配色": ["红金", "绿白", "蓝绿冷"],
                   "风格": ["手绘插画", "复古"], "元素": ["雪花", "驯鹿", "星星"],
                   "备注": "英国最大节日。圣诞市集 11 月下旬起，传统木质元素受欢迎"},
        "情人节": {"主题": ["情人节"], "配色": ["马卡龙粉", "暖橙红"],
                   "风格": ["手绘插画", "水彩"], "元素": ["爱心"], "备注": "礼品属性强"},
        "复活节": {"主题": ["复活节"], "配色": ["马卡龙粉", "绿白"],
                   "风格": ["卡通可爱", "水彩"], "元素": [], "备注": "兔子/彩蛋，3~4 月"},
        "母亲节": {"主题": ["母亲节", "花卉"], "配色": ["马卡龙粉", "莫兰迪"],
                   "风格": ["水彩", "手绘插画"], "元素": ["花卉"],
                   "备注": "⚠️ 英国母亲节在复活节前第 4 个周日，不是 5 月！"},
        "黑五": {"主题": ["白色星期五"], "配色": ["黑白灰"],
                 "风格": ["扁平矢量"], "元素": [], "备注": "折扣驱动"},
        "节礼日": {"主题": ["圣诞节"], "配色": ["红金"], "风格": ["复古"],
                   "元素": ["雪花"], "备注": "12/26，延续圣诞消费"},
        "新年": {"主题": ["新年"], "配色": ["红金"], "风格": ["扁平矢量"],
                 "元素": ["星星"], "备注": ""},
    },
    "US": {
        "万圣节": {"主题": ["万圣节"], "配色": ["暖橙红", "黑白灰"],
                   "风格": ["手绘插画", "卡通可爱"], "元素": ["南瓜", "幽灵"],
                   "备注": "10 月全月热销。儿童向要可爱化"},
        "感恩节": {"主题": ["感恩节", "秋季"],
                   "配色": ["大地色", "暖橙红", "金棕"],
                   "风格": ["手绘插画", "复古"], "元素": ["枫叶", "南瓜"],
                   "备注": "⚠️ 秋季通用款可共用，窗口 9~11 月，比万圣节长 6 倍"},
        "圣诞节": {"主题": ["圣诞节"], "配色": ["红金", "绿白"],
                   "风格": ["手绘插画", "卡通可爱"],
                   "元素": ["雪花", "驯鹿", "星星"], "备注": "11 月起热销，送礼强"},
        "情人节": {"主题": ["情人节"], "配色": ["马卡龙粉", "暖橙红"],
                   "风格": ["水彩", "手绘插画"], "元素": ["爱心"],
                   "备注": "礼品类，提前 2~3 周热销"},
        "独立日": {"主题": ["独立日"], "配色": ["蓝绿冷", "暖橙红"],
                   "风格": ["扁平矢量"], "元素": ["星星"],
                   "备注": "户外/野餐主题。6 月底~7 月初"},
        "母亲节": {"主题": ["母亲节", "花卉"], "配色": ["马卡龙粉"],
                   "风格": ["水彩"], "元素": ["花卉"], "备注": "5 月第 2 个周日"},
        "父亲节": {"主题": ["父亲节"], "配色": ["蓝绿冷", "大地色"],
                   "风格": ["扁平矢量"], "元素": [], "备注": "6 月第 3 个周日"},
        "复活节": {"主题": ["复活节"], "配色": ["马卡龙粉", "绿白"],
                   "风格": ["卡通可爱"], "元素": [], "备注": "春季"},
        "返校季": {"主题": ["返校季"], "配色": ["蓝绿冷"],
                   "风格": ["卡通可爱"], "元素": ["文字"], "备注": "整个 8 月"},
        "黑五": {"主题": ["白色星期五"], "配色": ["黑白灰"],
                 "风格": ["扁平矢量"], "元素": [], "备注": "全年最大促"},
        "网购星期一": {"主题": ["白色星期五"], "配色": ["黑白灰"],
                       "风格": ["扁平矢量"], "元素": [], "备注": "黑五后的周一"},
        "新年": {"主题": ["新年"], "配色": ["红金"], "风格": ["扁平矢量"],
                 "元素": ["星星"], "备注": ""},
    },
}

# 购物节的浮动日期规则
FLOAT_RULES = {
    "黑五": ("感恩节次日", 11, 3, 4, 1),        # 11月第4个周四 +1
    "网购星期一": ("感恩节后周一", 11, 3, 4, 4),
    "感恩节": ("11月第4个周四", 11, 3, 4, 0),
    "母亲节_US": ("5月第2个周日", 5, 6, 2, 0),
    "母亲节_GB": ("复活节前第4个周日", 0, 0, 0, 0),
    "父亲节": ("6月第3个周日", 6, 6, 3, 0),
    "白色星期五": ("11月第4个周五", 11, 4, 4, 0),
}

CN_NAME = {
    "New Year's Day": "新年", "New Year's Day (observed)": "新年",
    "Martin Luther King Jr. Day": "马丁路德金日", "Washington's Birthday": "总统日",
    "Memorial Day": "阵亡将士纪念日",
    "Juneteenth National Independence Day": "六月节", "Independence Day": "独立日",
    "Labor Day": "劳动节", "Columbus Day": "哥伦布日", "Veterans Day": "退伍军人节",
    "Thanksgiving Day": "感恩节", "Christmas Day": "圣诞节",
    "Good Friday": "耶稣受难日", "Easter Monday": "复活节",
    "Boxing Day": "节礼日", "May Day": "五朔节",
    "Spring Bank Holiday": "春季银行假日", "Summer Bank Holiday": "夏季银行假日",
    "Early May Bank Holiday": "五月初银行假日",
    # 沙特（阿拉伯语 -> 中文）
    "يوم التأسيسي": "建国日", "اليوم الوطني": "国庆日",
    "عطلة عيد الفطر": "开斋节", "عطلة عيد الفطر (يوم تعويضي)": "开斋节",
    "عطلة عيد الأضحى": "宰牲节", "عطلة عيد الأضحى (يوم تعويضي)": "宰牲节",
    "يوم عرفة": "阿拉法特日",
}


def easter_sunday(year: int) -> date:
    """算复活节周日（公历）。

    用 Anonymous Gregorian algorithm（Gauss 算法的标准形式）。
    复活节是天文历法（春分后第一个满月后的第一个周日），
    不能用"第几个周几"表示，必须算。

    实测：2026 -> 2026-04-05，2027 -> 2027-03-28，2028 -> 2028-04-16
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date | None:
    d = date(year, month, 1)
    cnt = 0
    while d.month == month:
        if d.weekday() == weekday:
            cnt += 1
            if cnt == n:
                return d
        d += timedelta(days=1)
    return None


def resolve_shopping(name: str, cc: str, year: int) -> date | None:
    """算购物节日期（浮动规则）。"""
    if name == "感恩节":
        return nth_weekday(year, 11, 3, 4)
    if name == "黑五":
        t = nth_weekday(year, 11, 3, 4)
        return t + timedelta(days=1) if t else None
    if name == "网购星期一":
        t = nth_weekday(year, 11, 3, 4)
        return t + timedelta(days=4) if t else None
    if name == "白色星期五":
        return nth_weekday(year, 11, 4, 4)          # 11月第4个周五
    # ---- 复活节（天文历法，必须算）----
    if name == "复活节":
        return easter_sunday(year)
    if name in ("耶稣受难日", "Good Friday"):
        return easter_sunday(year) - timedelta(days=2)
    if name in ("复活节周一", "Easter Monday"):
        return easter_sunday(year) + timedelta(days=1)

    if name == "母亲节":
        if cc == "GB":
            # 英国母亲节 = 复活节前第 4 个周日 = 复活节 - 21 天
            # （不用查 holidays 库 —— 它不一定有 Easter 条目，会导致返回 None）
            return easter_sunday(year) - timedelta(days=21)
        return nth_weekday(year, 5, 6, 2)
    if name == "父亲节":
        return nth_weekday(year, 6, 6, 3)
    if name == "返校季":
        return date(year, 8, 20)
    if name == "节礼日":
        return date(year, 12, 26)
    if name == "独立日":
        return date(year, 7, 4)
    if name == "情人节":
        return date(year, 2, 14)
    if name == "万圣节":
        return date(year, 10, 31)
    if name == "圣诞节":
        return date(year, 12, 25)
    if name == "新年":
        return date(year, 1, 1)
    return None


def scan(markets: list[str] | None = None, year: int = 0,
         lead: dict | None = None, horizon: int = 180,
         today: date | None = None) -> list[dict]:
    """扫描多个市场的档期。

    返回每行：{市场, 节日, 日期, 截止日(海运/空运), 剩余天, 状态, 标签, 关键词}

    关键设计：
      * **跨年自动顺延** —— 节点已过就用明年（否则"新年已错过"毫无意义）
      * **海运/空运各算截止日** —— 两种方式显示两个截止日期
      * 排序：能动手的按最晚截止日排，已过期的单独归到最后
    """
    today = today or date.today()
    lead = lead or {"海运": 45, "空运": 20}
    mks = markets or list(EVENT_TAGS.keys())
    out: list[dict] = []

    for cc in mks:
        mname = MARKETS.get(cc, {}).get("中文名", cc)
        cal_code = MARKETS.get(cc, {}).get("日历代码", cc)

        # 候选节点：中文名 -> 日期。跨年时两年都拉，取"下一个还没过的"
        for yr in (today.year, today.year + 1, today.year + 2):
            nodes: dict[str, date] = {}
            try:
                cal = hd.country_holidays(cal_code, years=yr)
                for d, n in cal.items():
                    zh = CN_NAME.get(str(n))
                    if zh and "(observed)" not in str(n):
                        nodes.setdefault(zh, d)
            except Exception:
                pass
            for s_ in MARKETS.get(cc, {}).get("购物节", []):
                d = resolve_shopping(s_, cc, yr)
                if d:
                    nodes[s_] = d

            for ev, d in sorted(nodes.items(), key=lambda kv: kv[1]):
                if not (EVENT_TAGS.get(cc, {}) or {}).get(ev):
                    continue
                if d < today:                      # 已过 -> 等明年那轮
                    continue
                if (d - today).days > horizon:
                    continue
                # 避免重复加入同一 (市场,节日,日期)
                if any(r["市场"] == cc and r["节日"] == ev and r["日期"] == d
                       for r in out):
                    continue
                out.append(_mk_row(cc, mname, ev, d, lead, today))
    # 排序：先按"还有多久要动手"，过期的排最后
    out.sort(key=lambda r: (r["级别"], r["紧急天"], r["市场"], r["日期"]))
    return out


def _mk_row(cc: str, mname: str, ev: str, d: date, lead: dict,
            today: date) -> dict:
    """构造一行。lead 里每种方式各算一个截止日。"""
    tags = (EVENT_TAGS.get(cc, {}) or {}).get(ev, {})
    # 每种运输方式的截止日与剩余天数
    by_mode = {}
    for mode, days in lead.items():
        ob = d - timedelta(days=days)
        by_mode[mode] = {"截止": ob, "剩余": (ob - today).days}
    # 用最紧的（剩余最少的）来判断紧急度
    tightest = min(by_mode.values(), key=lambda x: x["剩余"])
    left = tightest["剩余"]

    if left < 0:
        # 全都来不及了 —— 看是否还有某种方式能赶上
        can = [m for m, v in by_mode.items() if v["剩余"] >= 0]
        if can:
            st, lvl = f"仅{can[0]}还行", 1
            left = max(v["剩余"] for v in by_mode.values())
        else:
            st, lvl = "已错过", 3
    elif left <= 7:
        st, lvl = f"紧急·{left}天", 0
    elif left <= 30:
        st, lvl = f"该动手·{left}天", 0
    elif left <= 90:
        st, lvl = f"准备·{left}天", 1
    else:
        st, lvl = f"还早·{left}天", 2

    return {
        "市场": cc, "市场名": mname, "节日": ev, "日期": d,
        "截止": {m: v["截止"] for m, v in by_mode.items()},
        "剩余": {m: v["剩余"] for m, v in by_mode.items()},
        "紧急天": left if lvl < 3 else 9999,
        "状态": st, "级别": lvl,
        "主题": tags.get("主题", []), "配色": tags.get("配色", []),
        "风格": tags.get("风格", []), "元素": tags.get("元素", []),
        "备注": tags.get("备注", ""),
        "关键词": keywords_for(tags),
    }


def keywords_for(tags: dict, limit: int = 10) -> list[str]:
    kws = []
    for dim in ("主题", "元素", "风格", "配色"):
        for t in (tags.get(dim) or []):
            kws += TAG_KEYWORDS.get(t, [])
    seen, out = set(), []
    for k in kws:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out[:limit]


def load_markets() -> dict:
    """读配置里的市场注册表（不存在则用内置的）。"""
    if CFG.exists():
        try:
            c = json.loads(CFG.read_text(encoding="utf-8"))
            if c.get("市场注册表"):
                return c["市场注册表"]
        except Exception:
            pass
    return MARKETS
