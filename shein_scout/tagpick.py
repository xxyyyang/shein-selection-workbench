# -*- coding: utf-8 -*-
"""
shein_scout.tagpick —— 标签点亮页

交互（按用户想法）：
  * AI 已经给每张图打了标签
  * 页面按维度列出所有标签，标出每个标签有多少张图
  * **用户点亮喜欢的标签** —— 这就是审美权重表
  * 导出后成为下一批爬图的排序依据

设计要点：
  * 主题/配色维度**排在最前、视觉更突出**（用户明确要求主题权重最大）
  * 每个维度显示"已选 n 个"
  * 支持点两下加重权重（权重 2），再点一下取消
"""
from __future__ import annotations

import html as H
import json
from pathlib import Path

CSS = """
:root{--bg:#0e1013;--card:#161a20;--line:#252b35;--fg:#e9ecf1;--dim:#8b93a1;
--on:#4ade80;--on2:#22c55e;--acc:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.55 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:9;background:rgba(14,16,19,.97);
border-bottom:1px solid var(--line);padding:13px 20px;backdrop-filter:blur(8px)}
h1{margin:0 0 5px;font-size:17px}
.sub{color:var(--dim);font-size:12.5px}
.bar{margin-top:10px;display:flex;gap:9px;flex-wrap:wrap;align-items:center}
button{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:7px 13px;border-radius:8px;cursor:pointer;font-size:13px;font-family:inherit}
button:hover{background:#1d232d}
button.pri{background:#1d3557;border-color:#2c4a75}
main{padding:18px 20px 70px;max-width:1180px}
.dim{margin-bottom:26px}
.dim.pri-dim{border-left:3px solid var(--acc);padding-left:15px}
.dh{display:flex;align-items:baseline;gap:11px;margin-bottom:10px}
.dh h2{margin:0;font-size:15.5px}
.dh .cnt{color:var(--dim);font-size:12.5px}
.dh .w{font-size:11.5px;padding:1px 8px;border-radius:20px;background:#1c222c;
border:1px solid var(--line);color:var(--acc)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{border:1.5px solid var(--line);background:var(--card);border-radius:20px;
padding:6px 14px;cursor:pointer;user-select:none;font-size:13.5px;
transition:all .1s;display:flex;align-items:center;gap:7px}
.chip:hover{border-color:#3a4655}
.chip .n{color:var(--dim);font-size:11.5px}
.chip.w1{background:rgba(74,222,128,.16);border-color:var(--on);color:#c8f7d8}
.chip.w2{background:rgba(34,197,94,.34);border-color:var(--on2);color:#e6fff0;
font-weight:600}
.chip.w1 .n,.chip.w2 .n{color:#a7f3c4}
#out{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;z-index:99;
padding:40px;overflow:auto}
#out textarea{width:100%;height:50vh;background:#12151a;color:var(--fg);
border:1px solid var(--line);border-radius:9px;padding:13px;font:12.5px monospace}
.tip{color:var(--dim);font-size:12.5px;margin-top:9px}
"""

JS = """
const KEY='tagpick_'+BATCHKEY;
let picked={};
try{picked=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){picked={};}

document.querySelectorAll('.chip').forEach(c=>{
  c.addEventListener('click',()=>{
    const d=c.dataset.dim,t=c.dataset.tag;
    picked[d]=picked[d]||{};
    const cur=picked[d][t]||0;
    const nxt=(cur+1)%3;               // 0 -> 1 -> 2 -> 0
    if(nxt===0)delete picked[d][t]; else picked[d][t]=nxt;
    paint();
  });
});
function paint(){
  document.querySelectorAll('.chip').forEach(c=>{
    const d=c.dataset.dim,t=c.dataset.tag;
    const w=(picked[d]||{})[t]||0;
    c.classList.remove('w1','w2');
    if(w===1)c.classList.add('w1');
    if(w===2)c.classList.add('w2');
  });
  document.querySelectorAll('.dim').forEach(dim=>{
    const d=dim.dataset.dim;
    const n=Object.keys(picked[d]||{}).length;
    dim.querySelector('.cnt').textContent=n?('已选 '+n+' 个'):'未选';
  });
  const tot=Object.values(picked).reduce((a,o)=>a+Object.keys(o).length,0);
  document.getElementById('tot').textContent=tot;
  localStorage.setItem(KEY,JSON.stringify(picked));
}
function dump(){
  document.getElementById('jsonout').value=JSON.stringify(picked,null,1);
  document.getElementById('out').style.display='block';
}
function copyOut(){
  const t=document.getElementById('jsonout');t.select();
  document.execCommand('copy');alert('已复制，发我或存文件后跑 tagapply');
}
function dl(){
  const b=new Blob([document.getElementById('jsonout').value],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='tag_prefs.json';a.click();
}
function clr(){if(confirm('清空所有点亮？')){picked={};paint();}}
paint();
"""


def build(root: Path, out_html: Path, title: str = "点亮你喜欢的标签") -> Path:
    from . import library, tagger

    stats = library.all_tags(root)
    n_img = len([r for r in library.load(root) if r.tags])
    prefs = library.load_prefs(root)
    # 把已保存的偏好作为初始值
    init = json.dumps({d: {k: int(v) for k, v in vals.items()}
                       for d, vals in prefs.items()}, ensure_ascii=False)

    dims_html = []
    # 主题/配色优先展示
    order = ["节日主题", "配色风格", "适用季节", "画风", "图案密度", "画面元素"]
    dims_sorted = [d for d in order if d in stats] + \
                  [d for d in stats if d not in order]
    for d in dims_sorted:
        vals = stats[d]
        is_pri = d in ("节日主题", "配色风格")
        w = tagger.DIM_WEIGHT.get(d, 1.0)
        chips = "".join(
            f'<div class="chip" data-dim="{H.escape(d)}" data-tag="{H.escape(k)}">'
            f'{H.escape(k)}<span class="n">{v}</span></div>'
            for k, v in vals.items())
        dims_html.append(
            f'<div class="dim{" pri-dim" if is_pri else ""}" data-dim="{H.escape(d)}">'
            f'<div class="dh"><h2>{H.escape(d)}</h2>'
            f'<span class="cnt">未选</span>'
            f'<span class="w">排序权重 ×{w}</span></div>'
            f'<div class="chips">{chips}</div></div>')

    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<header>
 <h1>{title}</h1>
 <div class="sub">AI 已给 <b>{n_img}</b> 张图打好标签。
  点亮你喜欢的标签（<b>点一下</b>=喜欢，<b>再点一下</b>=很喜欢，<b>第三下</b>=取消）。
  已点亮 <b id="tot">0</b> 个。</div>
 <div class="bar">
  <button class="pri" onclick="dump()">导出点亮结果</button>
  <button onclick="clr()">清空</button>
 </div>
 <div class="tip">主题和配色权重最高 —— 它们决定排序的主要方向。
  其他维度是细节偏好，影响较小。</div>
</header>
<main>{''.join(dims_html)}</main>
<div id="out">
 <h2 style="margin:0 0 8px">点亮结果</h2>
 <p style="color:#8b93a1">复制或存成 json，然后跑：
  <code>python -m shein_scout tagapply &lt;文件&gt;</code></p>
 <textarea id="jsonout" readonly></textarea>
 <div style="margin-top:12px;display:flex;gap:9px">
  <button class="pri" onclick="copyOut()">复制</button>
  <button onclick="dl()">下载 json</button>
  <button onclick="document.getElementById('out').style.display='none'">关闭</button>
 </div>
</div>
<script>const BATCHKEY={json.dumps("all")};
let picked={json.dumps(init) and json.loads(init)};{JS}</script></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")
    return out_html
