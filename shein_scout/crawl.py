# -*- coding: utf-8 -*-
"""
shein_scout.crawl —— 网络图片搜集（只用允许自动访问的源）

合规边界（重要，请勿修改成"绕过反爬"）
--------------------------------------
* **不抓 SHEIN 前台**。实测 www.shein.com 返回 403，且批量抓取违反平台条款，
  你是平台商家，账号比数据值钱。SHEIN 侧请走开放平台 API 或自己的浏览行为。
* **只用图片搜索与开放图库**。Bing 图片搜索聚合的是公开可索引的图片；
  Pexels / Pixabay 是可免费使用的图库。
* **默认过滤掉带水印的付费图库域名**（iStock / Shutterstock / Getty / Freepik /
  Alamy / Dreamstime 等）—— 这类图有版权且带水印，拿来当参考容易踩坑。
* 抓取节奏有节制：单域名串行 + 请求间隔，不并发轰炸。

用法
----
    python -m shein_scout crawl "午休毯 白底 产品图" --limit 60 --out pool/网络候选
"""
from __future__ import annotations

import hashlib
import html as html_mod
import io
import json
import re
import time
import urllib.parse
from pathlib import Path

from . import features

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
}

# 带水印/有版权风险的付费图库：默认不下载
STOCK_HOSTS = (
    "istockphoto.com", "shutterstock.com", "gettyimages.", "freepik.com",
    "alamy.com", "dreamstime.com", "123rf.com", "depositphotos.com",
    "stock.adobe.com", "vectorstock.com", "canstockphoto.com", "fotolia.",
    "zcool.com.cn", "699pic.com", "veer.com", "hellorf.com", "tukuppt.com",
)

MAX_BYTES = 12 * 1024 * 1024


# --------------------------------------------------------------------------
# 搜索源适配
# --------------------------------------------------------------------------
def _get(url: str, timeout: int = 20, referer: str | None = None, binary: bool = False):
    import requests
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    r = requests.get(url, headers=h, timeout=timeout)
    r.raise_for_status()
    if not binary:
        r.encoding = r.apparent_encoding or r.encoding
    return r


def search_360(query: str, pages: int = 2) -> list[dict]:
    """360 图片搜索。走它的 JSON 接口（image.so.com/j），比解析 HTML 稳。"""
    import json as _json
    out: list[dict] = []
    for pg in range(pages):
        url = ("https://image.so.com/j?q=" + urllib.parse.quote(query) +
               f"&src=srp&correct=&sn={pg * 30}&pn=30")
        try:
            r = _get(url)
            data = _json.loads(r.text)
        except Exception as e:
            print(f"    [360] 第{pg + 1}页失败: {type(e).__name__}")
            continue
        for item in (data.get("list") or []):
            u = (item.get("img") or item.get("imgurl") or item.get("thumb") or "").strip()
            if u.startswith("http") and u not in [o["url"] for o in out]:
                out.append({"url": u, "source": "360"})
        time.sleep(0.6)
    return out


def search_bing(query: str, pages: int = 2, mkt: str = "") -> list[dict]:
    """Bing 图片搜索。mkt 可设 en-US 走国际版。返回 [{url, source}]。"""
    out: list[dict] = []
    for pg in range(pages):
        first = pg * 35 + 1
        url = ("https://www.bing.com/images/search?q=" +
               urllib.parse.quote(query) + f"&first={first}&count=35&form=HDRSC2")
        if mkt:
            url += f"&mkt={mkt}&setlang={mkt.split('-')[0]}"
        try:
            r = _get(url)
        except Exception as e:
            print(f"    [bing] 第{pg + 1}页失败: {type(e).__name__}")
            continue
        found = []
        for pat in (r'murl&quot;:&quot;(.*?)&quot;', r'"murl":"(.*?)"'):
            found += [html_mod.unescape(x) for x in re.findall(pat, r.text)]
        for u in found:
            if u.startswith("http") and u not in [o["url"] for o in out]:
                out.append({"url": u, "source": "bing"})
        time.sleep(0.8)
    return out


def search_pexels(query: str) -> list[dict]:
    """Pexels 搜索页静态 HTML（无需 key）。"""
    url = "https://www.pexels.com/search/" + urllib.parse.quote(query) + "/"
    r = _get(url)
    urls = re.findall(r'https://images\.pexels\.com/photos/[^"\'\s]+?\.(?:jpe?g|png)', r.text)
    # 去掉尺寸参数，取原图更清晰
    clean = []
    for u in urls:
        u = re.sub(r"\?.*$", "", u)
        if u not in clean:
            clean.append(u)
    return [{"url": u, "source": "pexels"} for u in clean]


def search_pixabay(query: str) -> list[dict]:
    """Pixabay 搜索页静态 HTML（无需 key）。"""
    url = "https://pixabay.com/images/search/" + urllib.parse.quote(query) + "/"
    r = _get(url)
    urls = re.findall(r'https://cdn\.pixabay\.com/photo/[^"\'\s]+?\.(?:jpe?g|png)', r.text)
    clean = []
    for u in urls:
        if u not in clean:
            clean.append(u)
    return [{"url": u, "source": "pixabay"} for u in clean]


SOURCES = {
    "bing": search_bing,
    "360": search_360,
    "pexels": search_pexels,
    "pixabay": search_pixabay,
}

DEFAULT_SOURCES = ("bing", "360")


def search(query: str, sources=DEFAULT_SOURCES, pages: int = 2) -> list[dict]:
    res: list[dict] = []
    seen: set[str] = set()
    for name in sources:
        fn = SOURCES.get(name)
        if not fn:
            continue
        try:
            got = fn(query, pages) if name in ("bing", "360") else fn(query)
        except Exception as e:
            print(f"  [{name}] 搜索失败: {type(e).__name__}: {str(e)[:80]}")
            continue
        new = 0
        for g in got:
            if g["url"] in seen:
                continue
            seen.add(g["url"])
            res.append(g)
            new += 1
        print(f"  [{name}] 新链接 {new} 条")
    return res


# --------------------------------------------------------------------------
# 下载 + 去重
# --------------------------------------------------------------------------
def _phash_of_bytes(data: bytes) -> str | None:
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im = im.convert("L").resize((9, 8), Image.BILINEAR)
        import numpy as np
        a = np.asarray(im, dtype=np.int16)
        bits = (a[:, 1:] > a[:, :-1]).ravel()
        return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"
    except Exception:
        return None


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except Exception:
        return 99


def is_stock_host(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(s in host for s in STOCK_HOSTS)


def download(cands: list[dict], out_dir: str | Path, limit: int = 60,
             min_side: int = 400, dup_hamming: int = 4,
             allow_stock: bool = False, delay: float = 0.35) -> dict:
    """下载候选图。返回统计信息。"""
    from PIL import Image
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "_meta.jsonl"
    stats = {"tried": 0, "saved": 0, "skipped_stock": 0, "failed": 0,
             "dup_exact": 0, "dup_near": 0, "too_small": 0}
    hashes: list[str] = []
    seen_sha: set[str] = set()
    # 载入已有哈希，支持多次运行累积而不重复
    if meta_path.exists():
        for line in meta_path.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)
                if m.get("sha1"):
                    seen_sha.add(m["sha1"])
                if m.get("phash"):
                    hashes.append(m["phash"])
            except Exception:
                pass

    for c in cands:
        if stats["saved"] >= limit:
            break
        url = c["url"]
        if is_stock_host(url) and not allow_stock:
            stats["skipped_stock"] += 1
            continue
        stats["tried"] += 1
        try:
            r = _get(url, timeout=25, referer=c.get("referer"), binary=True)
            data = r.content
            if len(data) > MAX_BYTES:
                stats["failed"] += 1
                continue
            sha = hashlib.sha1(data).hexdigest()
            if sha in seen_sha:
                stats["dup_exact"] += 1
                continue
            ph = _phash_of_bytes(data)
            if ph and any(hamming(ph, h) <= dup_hamming for h in hashes):
                stats["dup_near"] += 1
                continue
            im = Image.open(io.BytesIO(data))
            if min(im.width, im.height) < min_side:
                stats["too_small"] += 1
                continue
            ext = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp",
                   "BMP": ".bmp", "GIF": ".gif", "TIFF": ".tiff"}.get(im.format or "", ".jpg")
            name = f"{c.get('source', 'web')}_{stats['saved'] + 1:04d}_{sha[:8]}{ext}"
            fp = out / name
            fp.write_bytes(data)
            seen_sha.add(sha)
            if ph:
                hashes.append(ph)
            with open(meta_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"file": name, "url": url, "source": c.get("source"),
                                     "sha1": sha, "phash": ph,
                                     "w": im.width, "h": im.height}, ensure_ascii=False) + "\n")
            stats["saved"] += 1
            if stats["saved"] % 10 == 0:
                print(f"    已下载 {stats['saved']} 张…")
        except Exception:
            stats["failed"] += 1
        time.sleep(delay)
    return stats
