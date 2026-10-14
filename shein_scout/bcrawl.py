# -*- coding: utf-8 -*-
"""
shein_scout/bcrawl.py —— 浏览器爬虫（Playwright + 系统 Chrome）

为什么需要：亚马逊对 HTTP 请求返回空页（2271 字符），
但真实浏览器能拿到 331 个图链接。

实测（headless Chrome 131）：
  Amazon   ✅ 331 个
  Target   ✅  48 个
  Walmart  ❌ 0（有反爬）
  eBay     ❌ 0（有反爬）
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import quote_plus


# 亚马逊图片 URL 的规律：
#   .../images/I/<ID>._<修饰>.jpg   <- 各种尺寸
#   .../images/I/<ID>.jpg           <- 原图（最大）
# <ID> 是真正的图片标识，后面全是尺寸/格式修饰。
_AMZ_ID = re.compile(r"/images/I/([A-Za-z0-9+\-%]+?)(?:\._[^/]*)?\.jpg$")


def _norm_amazon(u: str) -> str:
    """把亚马逊的任何尺寸 URL 归一化成**原图 URL**。

    .../I/71VVnQ3u0WL._AC_UL320_.jpg            -> .../I/71VVnQ3u0WL.jpg
    .../I/71VVnQ3u0WL._AC_UL480_FMwebp_QL65_.jpg -> .../I/71VVnQ3u0WL.jpg

    这样同一张图的 5 个尺寸版本会归一成同一个 URL，去重才有效。
    """
    m = _AMZ_ID.search(u)
    if m:
        return f"https://m.media-amazon.com/images/I/{m.group(1)}.jpg"
    # 兜底：粗暴去掉所有 ._XXX 修饰
    return re.sub(r"\._[^/]*?\.jpg$", ".jpg", u)


def _launch(pw, headless: bool = True):
    """启动浏览器：优先用系统 Chrome（Playwright 自带版本常对不上）。"""
    args = ["--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage"]
    try:
        return pw.chromium.launch(headless=headless, channel="chrome", args=args)
    except Exception:
        return pw.chromium.launch(headless=headless, args=args)


def _fetch(pw, url: str, wait_ms: int = 2500, headless: bool = True):
    """用浏览器打开页面，返回 HTML。"""
    browser = _launch(pw, headless)
    try:
        ctx = browser.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"),
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.new_page()
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        return page.content()
    finally:
        try:
            browser.close()
        except Exception:
            pass


def _collect(html: str, pat: str, norm=None) -> list[str]:
    """按正则抓图 URL，去重（归一化后），保持顺序。"""
    out, seen = [], set()
    for m in re.finditer(pat, html):
        u = m.group(0)
        if u.endswith(".gif"):
            continue
        key = norm(u) if norm else u
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


# ---------------------------------------------------------------- Amazon
def amazon(keyword: str, want: int = 60, page: int = 1,
           headless: bool = True, verbose: bool = True) -> list[dict]:
    """从 Amazon 搜索页抓商品图。实测 331 个图链接 -> 去重后约 60 张。"""
    from playwright.sync_api import sync_playwright
    url = f"https://www.amazon.com/s?k={quote_plus(keyword)}&page={page}"
    if verbose:
        print(f"      [浏览器] Amazon ...")
    try:
        with sync_playwright() as pw:
            html = _fetch(pw, url, headless=headless)
    except Exception as e:
        if verbose:
            print(f"      Amazon 浏览器失败: {type(e).__name__}: {str(e)[:60]}")
        return []
    urls = _collect(html,
                    r"https://m\.media-amazon\.com/images/I/[A-Za-z0-9%+\-_.]+?\.jpg",
                    _norm_amazon)
    out = [{"img": u, "thumb": u, "title": keyword, "page": url,
            "width": 0, "height": 0} for u in urls[:want]]
    if verbose:
        print(f"      Amazon: 去重后 {len(out)} 张")
    return out


# ---------------------------------------------------------------- Target
def target(keyword: str, want: int = 60, headless: bool = True,
           verbose: bool = True) -> list[dict]:
    from playwright.sync_api import sync_playwright
    url = f"https://www.target.com/s?searchTerm={quote_plus(keyword)}"
    if verbose:
        print(f"      [浏览器] Target ...")
    try:
        with sync_playwright() as pw:
            html = _fetch(pw, url, headless=headless)
    except Exception as e:
        if verbose:
            print(f"      Target 失败: {type(e).__name__}")
        return []
    urls = _collect(html,
                    r"https://target\.scene7\.com/is/image/Target/[A-Za-z0-9%+\-_]+")
    out = [{"img": u + "?wid=2000&hei=2000&fit=constrain", "thumb": u,
            "title": keyword, "page": url, "width": 0, "height": 0}
           for u in urls[:want]]
    if verbose:
        print(f"      Target: {len(out)} 张")
    return out


SEARCHERS = {
    "amazon": amazon,
    "target": target,
}


def available() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except Exception:
        return False
