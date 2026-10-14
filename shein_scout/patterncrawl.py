# -*- coding: utf-8 -*-
"""
shein_scout.patterncrawl —— 图案爬取（为"学个人审美"服务）

设计目标（按用户要求）：
  * 按标签/关键词爬，每次 100+ 张
  * **图片存到本地**，方便逐个打标签
  * 每次爬取进入一个新批次，便于"一批批评审 → 逐步逼近个人审美"
  * 与之后的打分/学习打通：批次目录就是打标签的工作区

已有能力复用：
  * 360 图片 JSON 接口（每页 30~40 条，实测可用）
  * Bing 图片（需从 JS 里挖）
  * 特征提取（130+ 特征）与判别模型（纯 numpy Logit）
"""
from __future__ import annotations

import hashlib
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
]


@dataclass
class PatternImage:
    """一张爬到的图案图。"""
    batch: str = ""
    idx: int = 0
    keyword: str = ""
    source: str = ""
    title: str = ""
    page_url: str = ""          # 图片来源页（原图所在网页）
    img_url: str = ""           # 远程图片地址
    file: str = ""              # 本地文件名
    rel: str = ""               # 相对批次的路径（子目录/文件名）—— 唯一标识
    width: int = 0
    height: int = 0
    bytes: int = 0
    sha1: str = ""
    label: str = ""             # 用户标注：good / bad / ""（未标）
    note: str = ""
    extra: dict = field(default_factory=dict)


def _sess(referer: str = "") -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(UA_POOL),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    if referer:
        s.headers["Referer"] = referer
    return s


# ------------------------------------------------------------------ 360 图片
def search_360_images(keyword: str, want: int = 60, delay: float = 1.2,
                      verbose: bool = True) -> list[dict]:
    """360 图片搜索。返回 [{img, thumb, title, page, width, height}, ...]

    实测：JSON 接口 https://image.so.com/j 可用。
    """
    sess = _sess(referer="https://image.so.com/")
    out: list[dict] = []
    seen = set()
    page = 0
    while len(out) < want and page < 12:
        page += 1
        try:
            r = sess.get("https://image.so.com/j",
                         params={"q": keyword, "pn": 30, "src": "srp",
                                 "sn": (page - 1) * 30},
                         timeout=25)
            d = r.json()
        except Exception as e:
            if verbose:
                print(f"      360 第{page}页失败: {type(e).__name__}")
            break
        lst = d.get("list") or []
        if not lst:
            break
        for x in lst:
            # 实测：img/imgurl 常指向第三方原站（aliimg.com 等）且大量失效；
            # 360 自己 CDN 的 https 字段才稳。所以按可靠性排序取候选。
            cands = [
                str(x.get("https") or "").strip(),      # 360 CDN 原图（最可靠）
                str(x.get("img") or x.get("imgurl") or "").strip(),  # 原站（常失效）
                str(x.get("thumb") or "").strip(),      # 360 CDN 缩略图（兜底）
            ]
            cands = [c for c in cands if c.startswith("http")]
            if not cands:
                continue
            primary = cands[0]
            if primary in seen:
                continue
            seen.add(primary)
            # 原站域名黑名单：这些域的老图基本拿不到
            DEAD = ("aliimg.com", "alibaba.com", "alicdn.com", "taobao.com",
                    "chinadaily.com.cn")
            if any(d in primary for d in DEAD) and len(cands) > 1:
                primary = cands[1]
                if primary in seen:
                    continue
                seen.add(primary)
            out.append({
                "img": primary,
                "alt": cands[1] if len(cands) > 1 else "",
                "thumb": str(x.get("thumb") or ""),
                "title": str(x.get("title") or "")[:160],
                "page": str(x.get("link") or x.get("url") or ""),
                "width": x.get("width") or 0,
                "height": x.get("height") or 0,
            })
        if verbose:
            print(f"      360 第{page}页: 累计 {len(out)} 条")
        if len(out) >= want:
            break
        time.sleep(delay)
    return out[:want]


# ------------------------------------------------------------------ Bing 图片
def search_bing_images(keyword: str, want: int = 60, delay: float = 1.2,
                       verbose: bool = True) -> list[dict]:
    """Bing 图片。图片地址在页面的 murl 字段里，用正则挖出来。"""
    sess = _sess()
    out: list[dict] = []
    seen = set()
    first = 1
    while len(out) < want and first < 400:
        try:
            r = sess.get("https://www.bing.com/images/async",
                         params={"q": keyword, "first": first, "count": 35,
                                 "mmasync": 1},
                         timeout=25)
        except Exception as e:
            if verbose:
                print(f"      Bing 偏移{first} 失败: {type(e).__name__}")
            break
        # murl&quot;:&quot;http...&quot;
        urls = re.findall(r'murl(?:&quot;|")\s*:\s*(?:&quot;|")([^"&]+)', r.text)
        if not urls:
            urls = re.findall(r'"murl"\s*:\s*"([^"]+)"', r.text)
        titles = re.findall(r'"t"\s*:\s*"([^"]{4,200})"', r.text)
        if not urls:
            if verbose:
                print(f"      Bing 偏移{first}: 没挖到图片链接")
            break
        for i, u in enumerate(urls):
            if u in seen:
                continue
            seen.add(u)
            out.append({"img": u, "thumb": "",
                        "title": titles[i] if i < len(titles) else "",
                        "page": "", "width": 0, "height": 0})
        if verbose:
            print(f"      Bing 偏移{first}: 累计 {len(out)} 条")
        first += 35
        if len(out) >= want:
            break
        time.sleep(delay)
    return out[:want]


# ------------------------------------------------------------------ 电商平台
def search_amazon_products(keyword: str, want: int = 30,
                           verbose: bool = True) -> list[dict]:
    """从 Amazon 商品页抓图。

    优点：商品图是卖家自己拍的，**没有素材站水印**（通用图搜 35% 带水印）。
    实测：2 秒拿到 8 张。
    """
    from . import ecrawl
    try:
        return ecrawl.search_amazon(keyword, want=want, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"      Amazon 失败: {type(e).__name__}: {str(e)[:70]}")
        return []


def search_etsy_products(keyword: str, want: int = 30,
                         verbose: bool = True) -> list[dict]:
    from . import ecrawl
    try:
        return ecrawl.search_etsy(keyword, want=want, verbose=verbose)
    except Exception:
        return []


def search_aliexpress_products(keyword: str, want: int = 30,
                               verbose: bool = True) -> list[dict]:
    from . import ecrawl
    try:
        return ecrawl.search_aliexpress(keyword, want=want, verbose=verbose)
    except Exception:
        return []


def search_browser(keyword: str, site: str = "amazon", want: int = 60,
                   verbose: bool = True) -> list[dict]:
    """用**真实浏览器**（Playwright + 系统 Chrome）抓电商平台。

    为什么需要：亚马逊对普通 HTTP 请求返回空页，
    但真实浏览器能拿到 331 个图链接（实测）。

    可用站点：amazon / target
    """
    from . import bcrawl
    if not bcrawl.available():
        if verbose:
            print("      ⚠ 没装 playwright。运行: pip install playwright")
        return []
    fn = bcrawl.SEARCHERS.get(site)
    if not fn:
        if verbose:
            print(f"      未知站点: {site}")
        return []
    try:
        return fn(keyword, want=want, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"      浏览器抓取失败: {type(e).__name__}: {str(e)[:70]}")
        return []


SEARCHERS = {"bc-amazon": lambda kw, want=30, verbose=True: search_browser(kw, "amazon", want, verbose),
             "bc-target": lambda kw, want=30, verbose=True: search_browser(kw, "target", want, verbose),
             "amazon": search_amazon_products,
             "etsy": search_etsy_products,
             "aliexpress": search_aliexpress_products,
             "360": search_360_images, "bing": search_bing_images,
             "baidu": lambda kw, want=30, verbose=True: search_icrawler(kw, "baidu", want, verbose),
             "icbing": lambda kw, want=30, verbose=True: search_icrawler(kw, "icbing", want, verbose)}


# ------------------------------------------------------------------ icrawler
# 第三方成熟爬虫（github.com/hellock/icrawler）。
# 实测：Baidu 最快（6 秒 15 张），Bing 次之，Google 需 API key 不可用。
ICRAWLER_ENGINES = {
    "baidu": ("icrawler.builtin", "BaiduImageCrawler"),
    "icbing": ("icrawler.builtin", "BingImageCrawler"),
}


def search_icrawler(keyword: str, engine: str = "baidu", want: int = 30,
                    verbose: bool = True) -> list[dict]:
    """用 icrawler 搜图。它自带下载，所以这里直接把文件下到临时目录，
    再把路径作为 'local' 返回，由 download() 跳过重复下载。

    返回的每项形如 {"img": local_path, "local": True, ...}
    """
    import shutil
    import tempfile
    import time as _t

    spec = ICRAWLER_ENGINES.get(engine)
    if not spec:
        if verbose:
            print(f"      未知 icrawler 引擎: {engine}")
        return []
    mod, cls = spec
    try:
        m = __import__(mod, fromlist=[cls])
        C = getattr(m, cls)
    except Exception as e:
        if verbose:
            print(f"      icrawler 不可用（没装？）: {type(e).__name__}")
        return []

    tmp = Path(tempfile.mkdtemp(prefix="icr_"))
    try:
        crawler = C(feeder_threads=1, parser_threads=1, downloader_threads=3,
                    storage={"root_dir": str(tmp)})
        crawler.crawl(keyword=keyword, max_num=want)
    except Exception as e:
        if verbose:
            print(f"      icrawler 抓取失败: {type(e).__name__}: {str(e)[:80]}")
        return []

    exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
    files = sorted(f for f in tmp.rglob("*")
                   if f.is_file() and f.suffix.lower() in exts)
    out = []
    for f in files:
        try:
            from PIL import Image
            with Image.open(f) as im:
                w, h = im.size
        except Exception:
            continue
        out.append({"img": "", "local": str(f), "thumb": "",
                    "title": f"{keyword} ({engine})", "page": "",
                    "width": w, "height": h, "_tmpdir": str(tmp)})
    if verbose:
        print(f"      icrawler/{engine}: 拿到 {len(out)} 张")
    return out



# ------------------------------------------------------------------ 下载
def download(items: list[dict], out_dir: Path, batch: str, keyword: str,
             source: str, min_side: int = 400, delay: float = 0.35,
             verbose: bool = True, engine_tag: str = "") -> list[PatternImage]:
    """把图片下载到 out_dir。跳过太小/重复/失败的。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    sess = _sess(referer="https://image.so.com/")
    got: list[PatternImage] = []
    seen_sha = set()
    n_fail = n_small = n_dup = 0
    engine_tag = engine_tag or source

    for i, x in enumerate(items, 1):
        # icrawler 已经把文件下好了，直接搬过来
        if x.get("local"):
            try:
                src = Path(x["local"])
                data = src.read_bytes()
                used_url = f"local://{src.name}"
            except Exception:
                n_fail += 1
                continue
        else:
            data = None
            used_url = ""
        # 主地址失败时试备选（360 的原站链接常失效）
        tries = ([u for u in (x.get("img"), x.get("alt"))
                  if u and u.startswith("http")] if not x.get("local") else [])
        if not tries and data is None:
            n_fail += 1
            continue
        for u in tries:
            try:
                r = sess.get(u, timeout=20)
                if r.status_code == 200 and len(r.content) >= 3000:
                    data = r.content
                    used_url = u
                    break
            except Exception:
                continue
        if data is None:
            n_fail += 1
            continue
        url = used_url

        # 用 Pillow 校验并取尺寸
        try:
            import io

            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                fmt = (im.format or "JPEG").upper()
        except Exception:
            n_fail += 1
            continue
        if min(w, h) < min_side:
            n_small += 1
            continue
        sha = hashlib.sha1(data).hexdigest()
        if sha in seen_sha:
            n_dup += 1
            continue
        seen_sha.add(sha)

        ext = {"PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}.get(fmt, ".jpg")
        if x.get("local"):
            fn = f"{engine_tag}_{Path(x['local']).stem}{ext}"
        else:
            fn = f"{len(got)+1:04d}{ext}"
        (out_dir / fn).write_bytes(data)
        # rel 必须带上子目录：不同关键词/源的目录里文件名会重名（都从 0001 起）
        # 注意相对的是【批次目录】= out_dir.parent，不是 patterns/，
        # 否则会把批次名也包进 rel，导致拼路径时多一层（踩过这个坑）。
        try:
            rel = (out_dir / fn).relative_to(out_dir.parent).as_posix()
        except ValueError:
            rel = f"{out_dir.name}/{fn}"
        got.append(PatternImage(
            batch=batch, idx=len(got) + 1, keyword=keyword, source=source,
            title=x.get("title", ""), page_url=x.get("page", ""), img_url=url,
            file=fn, rel=rel, width=w, height=h, bytes=len(data), sha1=sha))
        if verbose and len(got) % 20 == 0:
            print(f"      已下载 {len(got)} 张 ...")
        time.sleep(delay)

    if verbose:
        print(f"      完成：下载 {len(got)} 张"
              f"（失败 {n_fail}、太小 {n_small}、重复 {n_dup}）")
    return got


def make_batch_name(keywords: list[str], when: str = "") -> str:
    """批次名 = 日期_关键词（便于在文件夹里一眼看出抓的是什么）。

    例：2026-10-13_ramadan_pattern
    """
    import re as _re
    when = when or time.strftime("%Y-%m-%d")
    kws = [k.strip() for k in keywords if k.strip()]
    if kws:
        # 取第一个关键词，去掉特殊字符，最长 24 字符
        safe = _re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", kws[0])[:24].strip("_")
        return f"{when}_{safe}" if safe else when
    return when


def crawl_batch(keywords: list[str], out_root: Path, want_per_kw: int = 60,
                sources: tuple[str, ...] = ("360", "bing"),
                min_side: int = 400, batch: str = "",
                verbose: bool = True,
                junk_filter: bool = True) -> tuple[str, list[PatternImage]]:
    """爬一批图。返回 (批次名, 图片列表)。

    batch 为空时自动用「日期_关键词」命名。
    """
    batch = batch or make_batch_name(keywords)
    all_imgs: list[PatternImage] = []
    for kw in keywords:
        if verbose:
            print(f"\n    关键词: {kw}")
        per_src = max(10, want_per_kw // max(1, len(sources)))
        for src in sources:
            fn = SEARCHERS.get(src)
            if not fn:
                continue
            if verbose:
                print(f"      源 {src}（目标 {per_src} 张）")
            try:
                found = fn(kw, want=per_src, verbose=verbose)
            except Exception as e:
                if verbose:
                    print(f"      {src} 搜索失败: {type(e).__name__}: {str(e)[:60]}")
                continue
            # 按关键词+源分目录存
            safe_kw = re.sub(r"[^\w\u4e00-\u9fff]+", "_", kw)[:40]
            sub = out_root / batch / f"{safe_kw}_{src}"
            all_imgs += download(found, sub, batch, kw, src,
                                 min_side=min_side, verbose=verbose)

    # 全批次去重（不同关键词可能搜到同图）
    uniq, seen = [], set()
    for im in all_imgs:
        if im.sha1 in seen:
            continue
        seen.add(im.sha1)
        uniq.append(im)

    # 内容级去重：同一张图的不同尺寸/轻微变体（sha1 不同但看着一样）
    # 亚马逊一个商品常有 5 个尺寸版本，URL 归一化后能去大部分，
    # 但仍可能有不同裁切/背景的变体，这里用感知哈希兜底。
    if len(uniq) > 1:
        try:
            from . import junk as _j
            base = out_root / batch
            pmap = {}
            for im in uniq:
                rel = (im.rel or im.file or "").replace("\\", "/")
                parts = [q for q in rel.split("/") if q and q != batch]
                pp = base.joinpath(*parts) if parts else None
                if pp and pp.exists():
                    pmap[pp] = im
            if len(pmap) > 1:
                groups = _j.find_near_duplicates(list(pmap), verbose=verbose)
                drop_paths = set()
                for grp in groups:
                    keep = max(grp, key=lambda q: q.stat().st_size)
                    for q in grp:
                        if q != keep:
                            drop_paths.add(q)
                if drop_paths:
                    for q in drop_paths:
                        try:
                            q.unlink()
                        except Exception:
                            pass
                    uniq = [im for im in uniq if pmap.get(
                        base / ((im.rel or im.file or "").replace("\\", "/")
                                .split("/")[-1]), im) not in
                        [pmap.get(q) for q in drop_paths]]
                    if verbose:
                        print(f"    内容去重: 剔掉 {len(drop_paths)} 张近似重复")
        except Exception as e:
            if verbose:
                print(f"    内容去重跳过: {type(e).__name__}: {e}")

    if verbose:
        print(f"\n    批次 {batch}: 共 {len(uniq)} 张（跨关键词去重后）")
    return batch, uniq


def save_manifest(imgs: list[PatternImage], out_root: Path, batch: str) -> Path:
    p = out_root / batch / "manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([asdict(x) for x in imgs], ensure_ascii=False, indent=1),
                 encoding="utf-8")
    return p


def load_manifest(out_root: Path, batch: str) -> list[PatternImage]:
    p = out_root / batch / "manifest.json"
    if not p.exists():
        return []
    return [PatternImage(**{k: v for k, v in d.items()
                            if k in PatternImage.__dataclass_fields__})
            for d in json.loads(p.read_text(encoding="utf-8"))]


def link_to_archive(imgs: list[PatternImage], out_root: Path, batch: str,
                    archive_root: Path, copy: bool = False) -> dict:
    """把一批图归入存档目录（用硬链接，不占额外空间；跨盘时自动改复制）。

    返回 {"dir": 存档目录, "linked": n, "copied": n, "failed": n}
    """
    import os
    import shutil

    dst = archive_root / batch
    dst.mkdir(parents=True, exist_ok=True)
    linked = copied = failed = 0
    for x in imgs:
        # 源文件：优先用 rel（相对批次），退回 file
        base = out_root / batch
        rel = (x.rel or x.file or "").replace("\\", "/")
        parts = [p for p in rel.split("/") if p and p != batch]
        src = base.joinpath(*parts) if parts else None
        if not src or not src.exists():
            failed += 1
            continue
        target = dst / src.name
        i = 1
        while target.exists():
            target = dst / f"{src.stem}_{i}{src.suffix}"
            i += 1
        try:
            if not copy:
                os.link(src, target)          # 硬链接：同一份数据两个名字
                linked += 1
            else:
                shutil.copy2(src, target)
                copied += 1
        except OSError:
            # 跨盘或不支持硬链接 -> 改复制
            try:
                shutil.copy2(src, target)
                copied += 1
            except Exception:
                failed += 1
        except Exception:
            failed += 1
    return {"dir": str(dst), "linked": linked, "copied": copied, "failed": failed}


def filter_watermarks(imgs: list, out_root: Path, batch: str,
                      verbose: bool = True) -> tuple[list, int]:
    """从刚抓的图里剔除带水印的（素材站样图，用了侵权）。

    返回 (保留下来的, 剔掉的数量)。
    这些图直接从批次目录删掉，不进库。
    """
    from . import junk as _junk

    base = out_root / batch
    keep, drop = [], 0
    for x in imgs:
        rel = (x.rel or x.file or "").replace("\\", "/")
        parts = [p for p in rel.split("/") if p and p != batch]
        p = base.joinpath(*parts) if parts else None
        if not p or not p.exists():
            keep.append(x)
            continue
        try:
            wm, ev = _junk.check_watermark(p)
        except Exception:
            wm, ev = False, ""
        if wm:
            drop += 1
            try:
                p.unlink()
            except Exception:
                pass
            if verbose and drop <= 5:
                print(f"      剔掉水印图: {p.name}  <- {ev}")
        else:
            keep.append(x)
    if verbose and drop:
        print(f"      水印过滤: 剔掉 {drop} 张，保留 {len(keep)} 张")
    return keep, drop
