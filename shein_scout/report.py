# -*- coding: utf-8 -*-
"""
shein_scout.report —— 生成自包含的 HTML 看图报告

设计要点：
* 缩略图以 base64 内嵌，报告是**单个文件**，可以直接发给同事或丢进微信，不依赖任何服务器。
* 默认按综合分从高到低排，支持按判定筛选、搜索文件名。
* 每张图下面列出"扣分原因 + 实测值 + 目标区间"，让你能自己复核结论，而不是信一个黑盒分数。
"""
from __future__ import annotations

import base64
import html
import io
from pathlib import Path

THUMB_W = 260
THUMB_Q = 72


def _thumb(path: str | Path, w: int = THUMB_W) -> str:
    try:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > w:
                h = max(1, int(im.height * w / im.width))
                im = im.resize((w, h), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=THUMB_Q, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


def _adjusted_previews(path: str | Path, factors: list[float],
                       w: int = 260) -> list[tuple[float, str]]:
    """生成降饱和预览的 data URI。返回 [(factor, data_uri)]。"""
    out: list[tuple[float, str]] = []
    try:
        from . import adjust
        prev = adjust.make_previews(path, factors, max_side=w * 2)
        # 注意排序：系数越小 = 降得越多 = 建议越强，所以要**升序**，
        # 让最强的那一档排在第一格。踩过的坑：原来用 reverse=True，
        # 结果用户看到的唯一一格是"降 4%"，而实际建议是"降 8%"。
        for f, data in sorted(prev.items()):
            uri = "data:image/jpeg;base64," + base64.b64encode(data).decode()
            out.append((f, uri))
    except Exception:
        pass
    return out


def _verdict_zh(v: str) -> str:
    return {"推荐": "推荐", "可上架": "可上架", "需修改": "需修改", "不建议": "不建议"}.get(v, v)


def build_report(rows: list[dict], out_path: str | Path,
                 profile: dict | None = None, model_info: dict | None = None,
                 title: str = "午休毯 · SHEIN 图片风格筛查报告",
                 max_thumbs: int = 400) -> Path:
    # 主序：综合分；次序：合规分；再次：文件名。
    # 为什么需要次序：没有画像时分数会被封顶（比如都在 74），只按主序会让排序失去区分度。
    rows = sorted(rows, key=lambda r: (-float(r.get("preference") or 0),
                                       -float(r.get("rule_score") or 0),
                                       str(r.get("name") or "")))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    n = len(rows)
    cnt = {"推荐": 0, "可上架": 0, "需修改": 0, "不建议": 0}
    for r in rows:
        cnt[r.get("verdict", "不建议")] = cnt.get(r.get("verdict", "不建议"), 0) + 1
    avg = sum(float(r.get("preference") or 0) for r in rows) / n if n else 0.0

    # 画像摘要
    prof_bits = []
    if profile:
        prof_bits.append(f"过审样本 {profile.get('n_approved', 0)} 张")
        prof_bits.append(f"被刷样本 {profile.get('n_rejected', 0)} 张")
        if profile.get("dominant_bg"):
            prof_bits.append({"white": "以白底为主", "scene": "以场景图为主",
                              "mixed": "白底与场景混用"}.get(profile["dominant_bg"], ""))
        if profile.get("style_cohesion") is not None:
            prof_bits.append(f"过审图风格一致性 {profile['style_cohesion']:.2f}")
    if model_info:
        prof_bits.append(model_info.get("desc", ""))

    cards = []
    for i, r in enumerate(rows):
        pref = float(r.get("preference") or 0)
        v = r.get("verdict", "")
        cls = r.get("verdict_class", "bad")
        img = _thumb(r.get("file", "")) if i < max_thumbs else ""
        img_html = (f'<img loading="lazy" src="{img}" alt="">' if img
                    else '<div class="noimg">无缩略图</div>')
        reasons = r.get("reasons") or []
        chips = []
        for it in reasons[:6]:
            lvl = it.get("level", "warn")
            got = it.get("got", "")
            want = it.get("want", "")
            extra = ""
            if got != "" or want != "":
                extra = f'<span class="kv">实测 {html.escape(str(got))} · 目标 {html.escape(str(want))}</span>'
            chips.append(f'<li class="{html.escape(lvl)}">{html.escape(str(it.get("msg", "")))}{extra}</li>')
        for it in (r.get("highlights") or [])[:2]:
            chips.append(f'<li class="good">{html.escape(str(it.get("msg", "")))}</li>')
        if not chips:
            chips.append('<li class="good">未发现明显问题</li>')

        subs = []
        if r.get("style_match") is not None:
            subs.append(html.escape(f'风格 {float(r["style_match"]):.0f}'))
        if r.get("model_score") is not None:
            subs.append(html.escape(f'模型 {float(r["model_score"]):.0f}'))
        subs.append(html.escape(f'规则 {float(r.get("rule_score") or 0):.0f}'))
        subs.append(html.escape(f'{r.get("width", "?")}×{r.get("height", "?")}'))
        if r.get("note"):
            # 注意：这里必须只转义 note 的**文本内容**，不能把外层的 <em> 一起转义。
            # 踩过的坑：原来下面用 ' · '.join(html.escape(s) for s in subs) 统一转义，
            # 结果整个 <em>⚠…</em> 变成 &lt;em&gt; 字面量显示在页面上。
            subs.append(f'<em>{html.escape(str(r["note"]))}</em>')
        # 降饱和对比条：只在"建议调色"时出现，避免报告噪音
        adj_html = ""
        adj = r.get("adjust") or {}
        if adj.get("needed") and adj.get("factors") and i < max_thumbs:
            prevs = _adjusted_previews(r.get("file", ""), list(adj["factors"])[:2])
            if prevs:
                cells = [f'<figure class="pcell"><img src="{src}" alt="">'
                         f'<figcaption>降饱和 {int(round((1 - f) * 100))}%</figcaption></figure>'
                         for f, src in prevs]
                adj_html = (
                    '<div class="adjust"><div class="adjtitle">'
                    f'建议降饱和（实测成功上架图饱和度 {adj.get("target")} 左右，'
                    f'当前 {adj.get("current")}）</div>'
                    f'<div class="pgrid">{"".join(cells)}</div>'
                    '<div class="adjnote">上面是按不同降幅生成的预览，可直接对比后再去 PS 里调'
                    '（图像 → 调整 → 色相/饱和度，只降"饱和度"滑块）</div></div>')
        name = r.get("name", "")
        cards.append(f"""
<figure class="card {cls}" data-v="{html.escape(v)}" data-name="{html.escape(name.lower())}">
  <div class="thumb">{img_html}<span class="badge">{pref:.0f}</span>
    <span class="tag">{html.escape(_verdict_zh(v))}</span></div>
  <figcaption>
    <div class="fname" title="{html.escape(str(r.get('file','')))}">{html.escape(str(name))}</div>
    <div class="sub">{' · '.join(subs)}</div>
    <ul class="reasons">{''.join(chips)}</ul>
    {adj_html}
  </figcaption>
</figure>""")

    # 没有画像时给出显式提示，避免"74 分到底算好还是不好"的困惑
    warn = ""
    if profile is not None and not (profile.get("stats") or {}):
        warn = ('<div class="warnbar">⚠ 尚未建立过审画像：本次只做硬规则合规检查，'
                '分数已封顶 74，任何图都不会出现「推荐」。'
                '把已过审/已上架的图放进 samples/过审 后运行 '
                '<code>python -m shein_scout build</code>，才能判断"像不像平台审美"。</div>')

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--bg:#0f1115;--card:#171a21;--line:#262b36;--txt:#e6e9ef;--dim:#9aa3b2;
--good:#2fbf71;--ok:#4c9aff;--warn:#f5a524;--bad:#f2545b;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--txt);
font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{padding:22px 26px;border-bottom:1px solid var(--line);position:sticky;top:0;
background:rgba(15,17,21,.95);backdrop-filter:blur(6px);z-index:9}}
h1{{margin:0 0 6px;font-size:19px;font-weight:600}}
.meta{{color:var(--dim);font-size:12.5px}}
.stats{{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:8px 13px;min-width:84px}}
.stat b{{display:block;font-size:19px;line-height:1.25}}
.stat span{{color:var(--dim);font-size:11.5px}}
.bar{{display:flex;gap:8px;margin-top:13px;flex-wrap:wrap;align-items:center}}
button{{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:7px;
padding:6px 13px;cursor:pointer;font-size:13px}}
button:hover{{border-color:#3a4354}}
button.on{{background:#233049;border-color:#3d5b8f}}
input[type=search]{{background:var(--card);color:var(--txt);border:1px solid var(--line);
border-radius:7px;padding:6px 11px;font-size:13px;min-width:180px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(252px,1fr));gap:15px;padding:20px 26px 60px}}
.card{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:11px;overflow:hidden}}
.card.bad{{border-left:3px solid var(--bad)}}
.card.warn{{border-left:3px solid var(--warn)}}
.card.ok{{border-left:3px solid var(--ok)}}
.card.good{{border-left:3px solid var(--good)}}
.thumb{{position:relative;background:#0b0d11;aspect-ratio:1/1;display:flex;
align-items:center;justify-content:center;overflow:hidden}}
.thumb img{{width:100%;height:100%;object-fit:contain}}
.noimg{{color:var(--dim);font-size:12px}}
.badge{{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.72);border-radius:6px;
padding:3px 9px;font-weight:700;font-size:16px}}
.tag{{position:absolute;top:8px;right:8px;background:rgba(0,0,0,.72);border-radius:6px;
padding:3px 8px;font-size:11.5px;color:var(--dim)}}
figcaption{{padding:10px 12px 13px}}
.fname{{font-size:12.5px;word-break:break-all;margin-bottom:3px}}
.sub{{color:var(--dim);font-size:11.5px;margin-bottom:7px}}
.reasons{{margin:0;padding:0;list-style:none}}
.reasons li{{font-size:12px;padding:4px 0 4px 13px;position:relative;border-top:1px solid #1f232c}}
.reasons li:first-child{{border-top:0}}
.reasons li:before{{content:"";position:absolute;left:2px;top:10px;width:5px;height:5px;border-radius:50%}}
.reasons li.bad:before{{background:var(--bad)}}
.reasons li.warn:before{{background:var(--warn)}}
.reasons li.good:before{{background:var(--good)}}
.reasons li.info:before{{background:var(--dim)}}
.kv{{display:block;color:var(--dim);font-size:11px;margin-top:2px}}
footer{{padding:0 26px 40px;color:var(--dim);font-size:12px}}
.warnbar{{margin:16px 26px 0;padding:11px 15px;border:1px solid #6b5320;background:#2a2113;
border-radius:9px;color:#f0d9a8;font-size:13px}}
.warnbar code{{background:#1a1610;padding:1px 6px;border-radius:4px;color:#ffd98a}}
.adjust{{margin-top:9px;border-top:1px solid #1f232c;padding-top:8px}}
.adjtitle{{font-size:11.5px;color:#f5a524;margin-bottom:6px}}
.adjnote{{font-size:10.5px;color:var(--dim);margin-top:5px;line-height:1.45}}
.pgrid{{display:grid;grid-template-columns:1fr 1fr;gap:6px}}
.pcell{{margin:0;background:#0b0d11;border-radius:6px;overflow:hidden}}
.pcell img{{width:100%;display:block}}
.pcell figcaption{{font-size:10.5px;color:var(--dim);text-align:center;padding:3px 0}}
</style></head><body>
<header>
  <h1>{html.escape(title)}</h1>
  <div class="meta">{html.escape(' · '.join([b for b in prof_bits if b]))}</div>
  <div class="stats">
    <div class="stat"><b>{n}</b><span>总图片</span></div>
    <div class="stat"><b>{cnt.get('推荐',0) + cnt.get('可上架',0)}</b><span>可直接用</span></div>
    <div class="stat"><b>{cnt.get('需修改',0)}</b><span>需修改</span></div>
    <div class="stat"><b>{cnt.get('不建议',0)}</b><span>不建议</span></div>
    <div class="stat"><b>{avg:.1f}</b><span>平均分</span></div>
  </div>
  <div class="bar">
    <button class="on" data-f="all">全部</button>
    <button data-f="推荐">推荐 {cnt.get('推荐',0)}</button>
    <button data-f="可上架">可上架 {cnt.get('可上架',0)}</button>
    <button data-f="需修改">需修改 {cnt.get('需修改',0)}</button>
    <button data-f="不建议">不建议 {cnt.get('不建议',0)}</button>
    <input type="search" id="q" placeholder="搜文件名…">
  </div>
</header>
{warn}
<div class="grid" id="grid">{''.join(cards)}</div>
<footer>按综合分从高到低排列。分数含义：推荐 ≥80 · 可上架 ≥66 · 需修改 ≥52 · 不建议 &lt;52。<br>
风格分来自你自己过审图统计出的画像；规则分是平台硬规范；模型分需有"过审 vs 被刷"两组样本才有意义。</footer>
<script>
let cur='all';
const q=document.getElementById('q');
function apply(){{
  document.querySelectorAll('.card').forEach(c=>{{
    const okv = cur==='all' || c.dataset.v===cur;
    const okq = !q.value || c.dataset.name.includes(q.value.toLowerCase());
    c.style.display = (okv&&okq) ? '' : 'none';
  }});
}}
document.querySelectorAll('button[data-f]').forEach(b=>b.onclick=()=>{{
  cur=b.dataset.f;
  document.querySelectorAll('button[data-f]').forEach(x=>x.classList.toggle('on',x===b));
  apply();
}});
q.oninput=apply;
</script>
</body></html>"""
    out.write_text(doc, encoding="utf-8")
    return out
