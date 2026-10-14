# -*- coding: utf-8 -*-
"""
shein_scout.productcrawl —— 竞品选品爬虫

用途（用户确认的范围）：
  1. 采集图案素材：拿到主图 URL，用户自己去下载
  2. 分析竞品：看别人什么款式卖得好
  3. 监控趋势：看最近什么主题在流行

产出：主图 URL + 商品链接 + 商品信息（标题/价格/评分/销量信号）
**不批量下载图片** —— 用户自己按需下载。

已实测可用的平台（2026-10-09 验证）：
  * Amazon 搜索页   200，每页 48 个商品，含 ASIN/主图/标题/评分/销量信号
  * 360 图片        JSON 接口，每页 40 条
  * Bing 图片       页面可访问但图片在 JS 里，需另想办法
受限：eBay(403) Walmart(验证码) Target(验证码) AliExpress/Temu(内容过少)
      Etsy/Google/DuckDuckGo(连不通)
不抓 SHEIN 前台（用户是平台商家，有账号风险）
"""
from __future__ import annotations

import html as htmlmod
import json
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import requests

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
]
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}


@dataclass
class Item:
    """一个竞品商品。"""
    source: str = ""
    keyword: str = ""
    rank: int = 0
    asin: str = ""
    title: str = ""
    url: str = ""
    main_image: str = ""          # 主图（大图 URL）
    thumb: str = ""               # 缩略图
    price: str = ""
    rating: str = ""
    reviews: str = ""
    bought: str = ""              # "X bought in past month" 的 X
    extra: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        return d


def _sess() -> requests.Session:
    s = requests.Session()
    s.headers.update(BASE_HEADERS)
    s.headers["User-Agent"] = random.choice(UA_POOL)
    return s


def _big(url: str) -> str:
    """把 Amazon 缩略图 URL 换成尽量大的版本。"""
    if not url:
        return ""
    # _AC_UL320_ / _AC_SX300_ 等 -> 用 _AC_UL1500_ 拿更大的图
    return re.sub(r"\._[A-Z0-9_,]+_\.(jpg|png|webp)$", r"._AC_UL1500_.\1", url)


# ---------------------------------------------------------------- Amazon
def page_quality(html: str) -> dict:
    """评估一页 Amazon 搜索页的质量。

    实测：Amazon 会间歇性返回面向中文用户的版本，特征是
      * 标题是机翻中文（对做欧美选品的用户没用）
      * 几乎没有 "X bought in past month"（最重要的排序依据）
    所以每次抓完都要校验，不合格就换会话重试。
    """
    import re as _re
    cjk = _re.compile(r"[\u4e00-\u9fff]")
    titles = _re.findall(r"<h2[^>]*>.*?<span[^>]*>([^<]{10,200})</span>", html, _re.S)
    n_bought = len(_re.findall(r"bought in past month", html, _re.I))
    n_cards = len(_re.findall(r'data-component-type="s-search-result"', html))
    n_cjk = sum(1 for t in titles if cjk.search(t))
    return {"cards": n_cards, "titles": len(titles), "cjk": n_cjk,
            "bought": n_bought,
            "good": n_cards >= 10 and n_bought >= 10 and n_cjk == 0}



def search_amazon(keyword: str, pages: int = 1, sort: str = "",
                  delay: float = 2.5, verbose: bool = True) -> list[Item]:
    """搜 Amazon 商品。

    sort: ""（相关度）| "review-rank"（按评价）| "price-asc-rank" | "price-desc-rank"
          | "date-desc-rank"（最新）
          Amazon 没有公开的"按销量"排序，但评论数和
          "X bought in past month" 就是销量信号。
    """
    items: list[Item] = []
    url = "https://www.amazon.com/s"
    for pg in range(1, pages + 1):
        params = {"k": keyword, "page": pg}
        if sort:
            params["s"] = sort

        # 每个页面最多试 4 次：Amazon 会间歇返回"机翻中文 + 无销量信号"的版本
        best_html, best_q = None, None
        for attempt in range(4):
            sess = _sess()
            if attempt:
                # 换会话、换 UA、清 cookie 再试
                sess.cookies.clear()
            try:
                r = sess.get(url, params=params, timeout=30)
            except Exception as e:
                if verbose:
                    print(f"    第{pg}页 第{attempt+1}次 请求失败: {type(e).__name__}")
                time.sleep(delay)
                continue
            if r.status_code != 200:
                if verbose:
                    print(f"    第{pg}页 第{attempt+1}次 HTTP {r.status_code}")
                time.sleep(delay)
                continue
            q = page_quality(r.text)
            if best_q is None or (q["bought"], -q["cjk"]) > (best_q["bought"], -best_q["cjk"]):
                best_html, best_q = r.text, q
            if q["good"]:
                break
            if verbose:
                print(f"    第{pg}页 第{attempt+1}次质量不合格"
                      f"（卡片{q['cards']} 销量信号{q['bought']} 中文标题{q['cjk']}），重试")
            time.sleep(delay + random.random() * 2)

        if best_html is None:
            break
        page_items = _parse_amazon(best_html, keyword)
        for it in page_items:
            it.rank = len(items) + 1
            items.append(it)
        if verbose:
            q = best_q or {}
            flag = "" if q.get("good") else "  ⚠ 该页质量偏低（销量信号少）"
            print(f"    Amazon 第{pg}页: {len(page_items)} 个商品"
                  f"（销量信号 {q.get('bought', 0)} 个）{flag}")
        if pg < pages:
            time.sleep(delay + random.random())
    return items


def _parse_amazon(html: str, keyword: str) -> list[Item]:
    """解析 Amazon 搜索页（用 lxml，比正则可靠）。

    实测确认的字段位置：
      * 商品卡片   : div[data-component-type="s-search-result"]，带 data-asin
      * 标题       : h2 span
      * 主图       : img.s-image 的 src（m.media-amazon.com/images/I/...）
      * 价格       : span.a-offscreen 全文（含货币符号，按 IP 可能是 JPY/USD/...）
      * 评论数     : a[aria-label="2,262 ratings"]
      * 评分       : span.a-icon-alt 或 "X out of 5 stars"
      * 销量信号   : 卡片文本里的 "X bought in past month"
    """
    try:
        from lxml import html as LH
    except ImportError:
        return _parse_amazon_regex(html, keyword)

    try:
        doc = LH.fromstring(html)
    except Exception:
        return []

    out: list[Item] = []
    seen = set()
    for card in doc.xpath('//div[@data-component-type="s-search-result"]'):
        asin = (card.get("data-asin") or "").strip()
        if not asin or asin in seen:
            continue
        seen.add(asin)
        it = Item(source="amazon", keyword=keyword, asin=asin,
                  url=f"https://www.amazon.com/dp/{asin}")

        # 标题
        for xp in ('.//h2//span/text()', './/h2//text()',
                   './/span[contains(@class,"a-text-normal")]/text()'):
            t = card.xpath(xp)
            if t:
                joined = htmlmod.unescape(re.sub(r"\s+", " ", " ".join(t)).strip())
                if len(joined) > 8:
                    it.title = joined
                    break

        # 主图：优先 img.s-image
        for xp in ('.//img[contains(@class,"s-image")]/@src',
                   './/img[contains(@class,"s-image")]/@data-image-latency',
                   './/img/@src'):
            src = card.xpath(xp)
            if src and "media-amazon" in src[0]:
                it.thumb = src[0]
                it.main_image = _big(src[0])
                break
        if not it.main_image:
            # 兜底：卡片里任意 media-amazon 图
            srcs = card.xpath('.//img/@src')
            for u in srcs:
                if "media-amazon" in u:
                    it.thumb = u
                    it.main_image = _big(u)
                    break

        # 价格：a-offscreen 全文。
        # 注意：Amazon 按**请求 IP** 决定货币，实测本机（日本 IP）返回 JPY，
        # 且 language/currency 参数无效（两种都试过）。所以价格是当地币种，
        # 跨市场比价时需要自行换算。
        po = card.xpath('.//span[contains(@class,"a-offscreen")]/text()')
        for v in po:
            v = (v or "").strip()
            if v and any(ch.isdigit() for ch in v):
                it.price = normalize_price(v)
                break
        if not it.price:
            whole = card.xpath('.//span[contains(@class,"a-price-whole")]/text()')
            sym = card.xpath('.//span[contains(@class,"a-price-symbol")]/text()')
            if whole:
                it.price = normalize_price(((sym[0] if sym else "") + whole[0]).strip())

        # 评论数：必须精确匹配 aria-label="2,262 ratings"
        # 踩过的坑：卡片里还有个 aria-label="4.5 out of 5 stars, rating details"，
        # 用 contains("rating") 会先匹配到它，取到 "4" 这个错误的数。
        rv = card.xpath('.//a[contains(@aria-label,"ratings")]/@aria-label')
        for v in rv:
            m = re.match(r"^\s*([\d,]+)\s+ratings?\s*$", v or "")
            if m:
                it.reviews = m.group(1)
                break
        if not it.reviews:
            # 兜底：显示文本形如 "(2.2K)" 的元素（其 aria-label 才是准确数）
            for el in card.xpath('.//*[contains(@class,"s-underline-text")]'):
                al = el.get("aria-label") or ""
                m = re.match(r"^\s*([\d,]+)\s+ratings?\s*$", al)
                if m:
                    it.reviews = m.group(1)
                    break

        # 评分
        rt = card.xpath('.//span[contains(@class,"a-icon-alt")]/text()')
        if rt:
            m = re.search(r"([\d.]+)", rt[0])
            if m:
                it.rating = m.group(1)
        if not it.rating:
            txt = card.text_content()
            m = re.search(r"([\d.]+)\s*out of 5 stars", txt)
            if m:
                it.rating = m.group(1)

        # 销量信号
        txt = card.text_content()
        m = re.search(r"([\d,.]+K?\+?)\s*bought in past month", txt, re.I)
        if m:
            it.bought = m.group(1)

        out.append(it)
    return out


def _parse_amazon_regex(html: str, keyword: str) -> list[Item]:
    """lxml 不可用时的兜底（用之前的正则实现）。"""
    blocks = re.split(r'(?=<div[^>]+data-asin="[A-Z0-9]{10}")', html)
    out: list[Item] = []
    seen = set()
    for b in blocks:
        m_asin = re.search(r'data-asin="([A-Z0-9]{10})"', b)
        if not m_asin:
            continue
        asin = m_asin.group(1)
        if asin in seen or "s-search-result" not in b[:4000]:
            continue
        seen.add(asin)
        it = Item(source="amazon", keyword=keyword, asin=asin,
                  url=f"https://www.amazon.com/dp/{asin}")
        t = re.search(r'<h2[^>]*>.*?<span[^>]*>([^<]{8,300})</span>', b, re.S)
        if t:
            it.title = htmlmod.unescape(re.sub(r"\s+", " ", t.group(1)).strip())
        imgs = re.findall(
            r'https://m\.media-amazon\.com/images/I/[A-Za-z0-9\-_.]+\._[A-Z0-9_,]+_\.(?:jpg|png)',
            b)
        if imgs:
            it.thumb = imgs[0]
            it.main_image = _big(imgs[0])
        po = re.search(r'<span class="a-offscreen">([^<]+)</span>', b)
        if po:
            it.price = htmlmod.unescape(po.group(1)).strip()
        rm = re.search(r"([\d.]+) out of 5 stars", b)
        if rm:
            it.rating = rm.group(1)
        rv = re.search(r'aria-label="([\d,]+) ratings', b)
        if rv:
            it.reviews = rv.group(1)
        bm = re.search(r"([\d,.]+K?\+?)\s*bought in past month", b, re.I)
        if bm:
            it.bought = bm.group(1)
        out.append(it)
    return out


# ---------------------------------------------------------------- 360 图片
def search_360(keyword: str, pages: int = 1, delay: float = 1.5,
               verbose: bool = True) -> list[Item]:
    """360 图片搜索（JSON 接口）。返回图片直链，适合采图案素材。"""
    sess = _sess()
    sess.headers["Referer"] = "https://image.so.com/"
    items: list[Item] = []
    for pg in range(1, pages + 1):
        try:
            r = sess.get("https://image.so.com/j",
                         params={"q": keyword, "pn": 30 * pg, "src": "srp"},
                         timeout=25)
            d = r.json()
        except Exception as e:
            if verbose:
                print(f"    360 第{pg}页失败: {type(e).__name__}")
            break
        lst = d.get("list") or []
        for x in lst:
            it = Item(source="360", keyword=keyword, rank=len(items) + 1)
            it.title = str(x.get("title") or "")[:200]
            it.main_image = str(x.get("img") or x.get("imgurl") or "")
            it.thumb = str(x.get("thumb") or "")
            it.url = str(x.get("url") or "")
            it.extra = {"width": x.get("width"), "height": x.get("height")}
            if it.main_image:
                items.append(it)
        if verbose:
            print(f"    360 第{pg}页: {len(lst)} 条")
        if pg < pages:
            time.sleep(delay)
    return items


SOURCES = {"amazon": search_amazon, "360": search_360}


def crawl(keywords: list[str], sources: tuple[str, ...] = ("amazon",),
          pages: int = 1, sort: str = "", delay: float = 2.5,
          verbose: bool = True) -> list[Item]:
    """按关键词列表爬多个源。"""
    all_items: list[Item] = []
    for kw in keywords:
        if verbose:
            print(f"\n  关键词: {kw}")
        for src in sources:
            fn = SOURCES.get(src)
            if not fn:
                continue
            if verbose:
                print(f"    源: {src}")
            try:
                if src == "amazon":
                    got = fn(kw, pages=pages, sort=sort, delay=delay, verbose=verbose)
                else:
                    got = fn(kw, pages=pages, delay=delay, verbose=verbose)
            except Exception as e:
                if verbose:
                    print(f"      {src} 出错: {type(e).__name__}: {str(e)[:60]}")
                got = []
            all_items.extend(got)
    return all_items


def dedup(items: list[Item]) -> list[Item]:
    """按 ASIN / 图片 URL 去重。"""
    seen, out = set(), []
    for it in items:
        k = it.asin or it.main_image
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def normalize_price(v: str) -> str:
    """统一币种写法：Amazon 同一批结果里会混用 'JPY 2,537' 和 '¥2,537'，
    其实是同一种货币，统一成国际代码更好读。"""
    import re as _re
    if not v:
        return v
    v = v.replace("\xa0", " ").strip()
    sym_map = {"¥": "JPY", "￥": "JPY", "$": "USD", "€": "EUR", "£": "GBP",
               "₩": "KRW", "₹": "INR"}
    m = _re.match(r"^([A-Za-z]{3})\s*(.+)$", v)
    if m:
        return f"{m.group(1)} {m.group(2).strip()}"
    m = _re.match(r"^([¥￥$€£₩₹])\s*(.+)$", v)
    if m:
        return f"{sym_map.get(m.group(1), m.group(1))} {m.group(2).strip()}"
    return v


def currency_of(items: list[Item]) -> str:
    """看这批商品的价格是什么币种（用于提示用户）。"""
    import re as _re
    for it in items:
        m = _re.match(r"^\s*([A-Za-z]{3}|[$€£¥₩])", it.price or "")
        if m:
            return m.group(1)
    return ""


def sort_by_popularity(items: list[Item]) -> list[Item]:
    """按销量信号 + 评论数排序（"只看卖得好的"）。"""
    def score(it: Item) -> float:
        s = 0.0
        b = (it.bought or "").replace(",", "").replace("+", "").strip()
        if b.upper().endswith("K"):
            try:
                s += float(b[:-1]) * 1000
            except ValueError:
                pass
        elif b:
            try:
                s += float(b)
            except ValueError:
                pass
        r = (it.reviews or "").replace(",", "")
        try:
            s += float(r) * 0.5
        except ValueError:
            pass
        return -s
    return sorted(items, key=score)


def save_json(items: list[Item], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([it.to_row() for it in items], ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p
