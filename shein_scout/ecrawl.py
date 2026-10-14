# -*- coding: utf-8 -*-
"""
电商平台图案爬虫 —— 从"卖毯子的商品页"里抓印花图。

为什么单独做：
  通用图搜（百度/必应）抓回来 35% 是素材站带水印的样图。
  而电商平台的商品图是**卖家自己拍的/授权的**，没有素材站水印。

支持（按可行性）：
  · Amazon   —— 商品图在 CDN 上，能直接抓（已有 productcrawl 基础）
  · AliExpress —— 有公开搜索接口
  · Etsy     —— 有公开搜索页

抓的是商品主图 + 商品详情图（可能包含印花平铺图）。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 现代浏览器都会发这些头。缺了会被判定为机器人（亚马逊就是这么封的）
BROWSER_HEADERS = {
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"),
    "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _sess() -> requests.Session:
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    s.headers.update({
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


# ---------------------------------------------------------------- Amazon
def search_amazon(keyword: str, want: int = 30, domain: str = "com",
                  verbose: bool = True) -> list[dict]:
    """从 Amazon 搜索页抓商品图。

    Amazon 的商品图在 m.media-amazon.com，尺寸可以靠替换文件名里的
    _AC_UL320_ 之类的标记来放大。
    """
    sess = _sess()
    out: list[dict] = []
    seen = set()
    for page in range(1, 4):
        url = (f"https://www.amazon.{domain}/s?k={quote_plus(keyword)}"
               f"&page={page}")
        try:
            r = sess.get(url, timeout=25)
        except Exception as e:
            if verbose:
                print(f"      Amazon 第{page}页失败: {type(e).__name__}")
            break
        if r.status_code != 200:
            if verbose:
                print(f"      Amazon 第{page}页 HTTP {r.status_code}")
            break
        html = r.text
        # 抓所有商品图链接
        for m in re.finditer(
                r'https://m\.media-amazon\.com/images/I/[A-Za-z0-9%+\-_.]+?\.jpg',
                html):
            u = m.group(0)
            # 放大：去掉 _AC_UL320_ 这类缩略标记
            big = re.sub(r"\._[A-Z0-9_,_]+\.jpg$", ".jpg", u)
            if big in seen:
                continue
            seen.add(big)
            out.append({"img": big, "thumb": u, "title": keyword,
                        "page": url, "width": 0, "height": 0})
            if len(out) >= want:
                break
        if verbose:
            print(f"      Amazon 第{page}页: 累计 {len(out)} 张")
        if len(out) >= want:
            break
        time.sleep(1.2 + (page * 0.4))
    return out


# ---------------------------------------------------------------- AliExpress
def search_aliexpress(keyword: str, want: int = 30,
                      verbose: bool = True) -> list[dict]:
    """从速卖通搜索页抓商品图（图在 alicdn.com）。"""
    sess = _sess()
    out: list[dict] = []
    seen = set()
    url = f"https://www.aliexpress.com/wholesale?SearchText={quote_plus(keyword)}"
    try:
        r = sess.get(url, timeout=25)
        if r.status_code != 200:
            if verbose:
                print(f"      AliExpress HTTP {r.status_code}")
            return []
        html = r.text
        for m in re.finditer(
                r'https://ae\d*\.alicdn\.com/kf/[A-Za-z0-9%+\-_.]+\.(?:jpg|png|webp)',
                html):
            u = m.group(0)
            if u in seen:
                continue
            seen.add(u)
            out.append({"img": u, "thumb": u, "title": keyword,
                        "page": url, "width": 0, "height": 0})
            if len(out) >= want:
                break
    except Exception as e:
        if verbose:
            print(f"      AliExpress 失败: {type(e).__name__}")
    if verbose:
        print(f"      AliExpress: 共 {len(out)} 张")
    return out


# ---------------------------------------------------------------- Etsy
def search_etsy(keyword: str, want: int = 30,
                verbose: bool = True) -> list[dict]:
    """从 Etsy 搜索页抓商品图（图在 etsystatic.com）。"""
    sess = _sess()
    out: list[dict] = []
    seen = set()
    url = f"https://www.etsy.com/search?q={quote_plus(keyword)}"
    try:
        r = sess.get(url, timeout=25)
        if r.status_code != 200:
            if verbose:
                print(f"      Etsy HTTP {r.status_code}")
            return []
        html = r.text
        for m in re.finditer(
                r'https://i\.etsystatic\.com/[A-Za-z0-9%+\-_./]+?\.(?:jpg|png)',
                html):
            u = m.group(0)
            # 大图：把 il_340x270 换成 il_fullxfull
            big = re.sub(r"il_[0-9]+x[0-9]+", "il_fullxfull", u)
            if big in seen:
                continue
            seen.add(big)
            out.append({"img": big, "thumb": u, "title": keyword,
                        "page": url, "width": 0, "height": 0})
            if len(out) >= want:
                break
    except Exception as e:
        if verbose:
            print(f"      Etsy 失败: {type(e).__name__}")
    if verbose:
        print(f"      Etsy: 共 {len(out)} 张")
    return out


def search_target(keyword: str, want: int = 30,
                  verbose: bool = True) -> list[dict]:
    """从 Target 搜索页抓商品图。

    实测可用（48 个图链接）。图在 target.scene7.com，
    可以加 ?wid=2000 参数拿到大图。
    """
    sess = _sess()
    out, seen = [], set()
    url = f"https://www.target.com/s?searchTerm={quote_plus(keyword)}"
    try:
        r = sess.get(url, timeout=30)
    except Exception as e:
        if verbose:
            print(f"      Target 失败: {type(e).__name__}")
        return []
    if r.status_code != 200 or len(r.text) < 20000:
        if verbose:
            print(f"      Target 被拦（{r.status_code}, {len(r.text)} 字符）")
        return []
    pat = (r"https://target\.scene7\.com/is/image/Target/"
           r"[A-Za-z0-9%+\-_?=&.]+")
    for m in re.finditer(pat, r.text):
        u = m.group(0).split("?")[0]
        if u in seen:
            continue
        seen.add(u)
        # 加参数拿大图
        big = u + "?wid=2000&hei=2000&fit=constrain"
        out.append({"img": big, "thumb": u + "?wid=300",
                    "title": keyword, "page": url, "width": 0, "height": 0})
        if len(out) >= want:
            break
    if verbose:
        print(f"      Target: 共 {len(out)} 张")
    return out


SEARCHERS = {
    "target": search_target,
    "amazon": search_amazon,
    "aliexpress": search_aliexpress,
    "etsy": search_etsy,
}
