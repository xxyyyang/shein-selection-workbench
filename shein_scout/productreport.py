# -*- coding: utf-8 -*-
"""
竞品选品爬虫的 CLI 命令 + HTML 仪表盘。

给 shein_scout 加 productcrawl 子命令：
  python -m shein_scout productcrawl "fall throw blanket" -n 3 --sort best
产出：
  * products.csv   —— 商品信息（标题/价格/评分/评论数/销量信号/链接）
  * dashboard.html —— 卡片式仪表盘，可直接看图、点链接跳 Amazon
  * 主图不下载（用户自己去下）
"""
from __future__ import annotations

import csv
import html as H
import time
from pathlib import Path

DASH_CSS = """
:root{--bg:#0f1115;--card:#181b22;--line:#272b35;--fg:#e8eaed;--dim:#9aa0a6;
--hi:#7ee787;--mid:#ffd166;--lo:#ff7b72;--acc:#58a6ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;background:rgba(15,17,21,.95);border-bottom:1px solid var(--line);
padding:14px 20px;z-index:10;backdrop-filter:blur(6px)}
h1{margin:0 0 6px;font-size:19px}
.meta{color:var(--dim);font-size:12.5px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.bar input,.bar select{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:7px 10px;border-radius:7px;font-size:13px}
.bar input{min-width:260px}
.wrap{padding:18px 20px 60px}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;overflow:hidden;
display:flex;flex-direction:column}
.thumb{width:100%;aspect-ratio:1;object-fit:contain;background:#fff;display:block}
.body{padding:11px 12px;display:flex;flex-direction:column;gap:7px;flex:1}
.title{font-size:13px;line-height:1.4;max-height:3.9em;overflow:hidden}
.stats{display:flex;gap:9px;flex-wrap:wrap;font-size:12px;color:var(--dim)}
.stats b{color:var(--fg);font-weight:600}
.sig{font-size:12px;padding:3px 8px;border-radius:20px;background:#1d2430;
border:1px solid var(--line);color:var(--hi);align-self:flex-start}
.acts{display:flex;gap:7px;margin-top:auto;padding-top:2px}
.acts a{flex:1;text-align:center;text-decoration:none;font-size:12px;padding:6px 4px;
border-radius:7px;border:1px solid var(--line);color:var(--acc);background:#141922}
.acts a:hover{background:#1b2331}
.acts a.img{color:var(--hi)}
.rank{position:absolute;margin:7px;background:rgba(0,0,0,.72);color:#fff;font-size:11.5px;
padding:2px 7px;border-radius:6px}
.pw{position:relative}
footer{color:var(--dim);font-size:12px;padding:0 20px 40px}
.note{background:#151a21;border:1px solid var(--line);border-left:3px solid var(--acc);
padding:10px 13px;border-radius:8px;margin-bottom:16px;font-size:13px;color:var(--dim)}
"""

DASH_JS = """
const q=document.getElementById('q'),so=document.getElementById('so'),
cards=[...document.querySelectorAll('.card')];
function apply(){
  const t=q.value.trim().toLowerCase();
  const s=so.value;
  cards.forEach(c=>{
    const hit=!t||c.dataset.t.includes(t);
    c.style.display=hit?'flex':'none';
  });
  const vis=cards.filter(c=>c.style.display!=='none');
  if(s!=='keep'){
    const g=document.querySelector('.grid');
    vis.sort((a,b)=>{
      const A=+a.dataset[s]||0,B=+b.dataset[s]||0;
      return B-A;
    }).forEach(c=>g.appendChild(c));
  }
  document.getElementById('cnt').textContent=vis.length;
}
q.addEventListener('input',apply);so.addEventListener('change',apply);
apply();
"""


def write_dashboard(items, out_html: Path, title: str, currency: str = "") -> Path:
    """生成卡片式仪表盘。图片走远程 URL（不下载），点卡片按钮跳 Amazon。"""
    now = time.strftime("%Y-%m-%d %H:%M")
    kw = ", ".join(sorted({it.keyword for it in items if it.keyword}))

    def num(it, k):
        """把 '10K+' / '2,262' 变成可排序的数。"""
        v = getattr(it, k, "") or ""
        v = v.replace(",", "").replace("+", "").strip()
        try:
            if v.upper().endswith("K"):
                return float(v[:-1]) * 1000
            return float(v)
        except ValueError:
            return 0.0

    cards = []
    for it in items:
        sig = ""
        if it.bought:
            sig = f"近月 {it.bought} 人买过"
        elif it.reviews:
            sig = f"{it.reviews} 条评价"
        img = H.escape(it.main_image or it.thumb or "")
        cards.append(f"""<div class="card" data-t="{H.escape((it.title or '').lower())}"
 data-bought="{num(it,'bought')}" data-reviews="{num(it,'reviews')}"
 data-rating="{num(it,'rating')}" data-rank="{100000-it.rank}">
 <div class="pw"><span class="rank">#{it.rank}</span>
  <img class="thumb" loading="lazy" src="{img}" alt=""></div>
 <div class="body">
  <div class="title">{H.escape(it.title or '(无标题)')}</div>
  <div class="stats">
   <span>价格 <b>{H.escape(it.price or '-')}</b></span>
   <span>评分 <b>{H.escape(it.rating or '-')}</b></span>
   <span>评价 <b>{H.escape(it.reviews or '-')}</b></span>
  </div>
  {f'<div class="sig">{H.escape(sig)}</div>' if sig else ''}
  <div class="acts">
   <a href="{H.escape(it.url)}" target="_blank" rel="noopener">打开商品页</a>
   <a class="img" href="{img}" target="_blank" rel="noopener" download>下载主图</a>
  </div>
 </div></div>""")

    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{H.escape(title)}</title><style>{DASH_CSS}</style></head><body>
<header>
 <h1>{H.escape(title)}</h1>
 <div class="meta">关键词：{H.escape(kw or '-')} · 共 <b id="cnt">{len(items)}</b> 个商品
  · 币种 {H.escape(currency or '未知')}（Amazon 按你的网络位置返回当地币种）
  · 生成于 {now}</div>
 <div class="bar">
  <input id="q" placeholder="筛选标题关键词，如 maple / ghost / plaid">
  <select id="so">
   <option value="rank">默认顺序（搜索相关度）</option>
   <option value="bought">按销量信号排序</option>
   <option value="reviews">按评价数排序</option>
   <option value="rating">按评分排序</option>
  </select>
 </div>
</header>
<div class="wrap">
 <div class="note">
  <b>用法</b>：点「打开商品页」去 Amazon 看详情，点「下载主图」直接存原图。
  图片用远程链接显示，<b>本地没有下载任何图片</b>。
  排序依据：<b>销量信号 = "X bought in past month"</b>（Amazon 官方显示的近期购买人数），
  比评论数更能反映当前热度。
 </div>
 <div class="grid">{''.join(cards)}</div>
</div>
<footer>由 shein_scout productcrawl 生成 · 数据来自 Amazon 公开搜索页 · 仅供选品参考</footer>
<script>{DASH_JS}</script></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")
    return out_html


def write_csv(items, out_csv: Path) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["排名", "来源", "关键词", "ASIN", "标题", "价格", "评分",
                    "评论数", "近月购买", "商品链接", "主图链接"])
        for it in items:
            w.writerow([it.rank, it.source, it.keyword, it.asin, it.title,
                        it.price, it.rating, it.reviews, it.bought,
                        it.url, it.main_image])
    return out_csv
