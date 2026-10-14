# -*- coding: utf-8 -*-
"""
shein_scout.libraryview —— 图案库页面

功能（按用户要求）：
  * 按标签搜索（好 / 坏 / 跳过 / 未标）
  * 按关键词、备注搜索
  * 标记"已用"
  * 导出（好的图案清单 / 全部）

页面自包含，图片走本地相对路径。
标记操作存在浏览器本地，通过"导出变更"把结果给我，再写回库。
"""
from __future__ import annotations

import html as H
import json
from pathlib import Path

CSS = """
:root{--bg:#0e1013;--card:#161a20;--line:#252b35;--fg:#e9ecf1;--dim:#8b93a1;
--good:#4ade80;--bad:#f87171;--skip:#fbbf24;--acc:#60a5fa;--used:#a78bfa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:20;background:rgba(14,16,19,.97);
border-bottom:1px solid var(--line);padding:12px 18px;backdrop-filter:blur(8px)}
h1{margin:0 0 8px;font-size:17px}
.stats{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:9px}
.pill{padding:3px 11px;border-radius:20px;background:#1c222c;font-size:12.5px;
border:1px solid var(--line)}
.pill b{color:#fff}
.pill.good b{color:var(--good)}.pill.bad b{color:var(--bad)}
.pill.skip b{color:var(--skip)}.pill.used b{color:var(--used)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select,button{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:7px 11px;border-radius:8px;font-size:13px;font-family:inherit}
input{min-width:210px}
button{cursor:pointer}button:hover{background:#1d232d}
button.pri{background:#1d3557;border-color:#2c4a75}
main{padding:16px 18px 60px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(215px,1fr))}
.cell{background:var(--card);border:2px solid var(--line);border-radius:11px;
overflow:hidden;position:relative;display:flex;flex-direction:column}
.cell.good{border-color:var(--good)}.cell.bad{border-color:var(--bad)}
.cell.skip{border-color:var(--skip)}
.cell.used{opacity:.55}
.cell img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#fff}
.badges{position:absolute;top:6px;left:6px;display:flex;gap:5px;flex-wrap:wrap}
.b{font-size:11px;padding:1px 7px;border-radius:6px;font-weight:600}
.b.good{background:var(--good);color:#04220f}
.b.bad{background:var(--bad);color:#2a0707}
.b.skip{background:var(--skip);color:#2a1e02}
.b.used{background:var(--used);color:#1b0a2e}
.b.pred{background:#243244;color:#93c5fd;border:1px solid #2f4a68}
.cap{padding:7px 9px;font-size:11.5px;color:var(--dim);flex:1}
.acts{display:flex;gap:5px;padding:0 9px 9px}
.acts button{flex:1;font-size:11.5px;padding:5px 3px}
.empty{color:var(--dim);padding:40px;text-align:center;font-size:15px}
#out{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;z-index:99;
padding:40px;overflow:auto}
#out textarea{width:100%;height:52vh;background:#12151a;color:var(--fg);
border:1px solid var(--line);border-radius:9px;padding:13px;font:12.5px monospace}
"""

JS = """
let marks={};                       // {key: {used:bool, note:str}}
const grid=document.getElementById('grid');
const all=[...document.querySelectorAll('.cell')];
function persist(){localStorage.setItem('lib_marks',JSON.stringify(marks));}
try{marks=JSON.parse(localStorage.getItem('lib_marks')||'{}');}catch(e){marks={};}

function filt(){
  const lab=document.getElementById('f-label').value;
  const kw=document.getElementById('f-kw').value.trim().toLowerCase();
  const tx=document.getElementById('f-text').value.trim().toLowerCase();
  const us=document.getElementById('f-used').value;
  let n=0;
  all.forEach(c=>{
    const d=c.dataset;
    const m=marks[d.key]||{};
    const used=(m.used!==undefined)?m.used:(d.used==='1');
    let ok=true;
    if(lab==='un'&&d.label)ok=false;
    else if(lab&&lab!=='un'&&d.label!==lab)ok=false;
    if(ok&&kw&&!(d.kw||'').toLowerCase().includes(kw))ok=false;
    if(ok&&tx&&!(d.title||'').toLowerCase().includes(tx)&&
       !(m.note||'').toLowerCase().includes(tx))ok=false;
    if(ok&&us==='yes'&&!used)ok=false;
    if(ok&&us==='no'&&used)ok=false;
    c.style.display=ok?'flex':'none';
    if(ok)n++;
  });
  document.getElementById('shown').textContent=n;
}
['f-label','f-kw','f-text','f-used'].forEach(id=>{
  const el=document.getElementById(id);
  el.addEventListener('input',filt);el.addEventListener('change',filt);
});
function toggleUsed(key){
  const c=document.querySelector(`.cell[data-key="${CSS.escape(key)}"]`);
  const d=c.dataset;
  const m=marks[key]||{};
  const cur=(m.used!==undefined)?m.used:(d.used==='1');
  m.used=!cur;marks[key]=m;persist();
  c.classList.toggle('used',m.used);
  let b=c.querySelector('.b.used');
  if(m.used&&!b){b=document.createElement('span');b.className='b used';
    b.textContent='已用';c.querySelector('.badges').appendChild(b);}
  if(!m.used&&b)b.remove();
  filt();
}
function setNote(key){
  const c=document.querySelector(`.cell[data-key="${CSS.escape(key)}"]`);
  const m=marks[key]||{};
  const v=prompt('备注（比如：适合秋季款 / 客户要的）',m.note||'');
  if(v===null)return;
  m.note=v;marks[key]=m;persist();
  c.querySelector('.cap .note').textContent=v||'';
}
function dump(){
  const rows=[];
  all.forEach(c=>{
    const d=c.dataset,m=marks[d.key];
    if(!m)return;
    rows.push({batch:d.batch,rel:d.rel,file:d.file,keyword:d.kw,
      used:!!m.used,note:m.note||''});
  });
  document.getElementById('jsonout').value=JSON.stringify(rows,null,1);
  document.getElementById('out').style.display='block';
  document.getElementById('nchg').textContent=rows.length;
}
function copyOut(){
  const t=document.getElementById('jsonout');t.select();
  document.execCommand('copy');alert('已复制，粘贴给我即可');
}
function dl(){
  const b=new Blob([document.getElementById('jsonout').value],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='library_marks.json';a.click();
}
function copyKeys(){
  const keys=all.filter(c=>c.style.display!=='none')
    .map(c=>c.dataset.rel||c.dataset.file).join('\\n');
  navigator.clipboard.writeText(keys).then(()=>alert('已复制当前筛选结果的 '
    +keys.split('\\n').length+' 个文件名'));
}
// 初始化已用状态
all.forEach(c=>{
  const m=marks[c.dataset.key];
  const used=m&&m.used!==undefined?m.used:c.dataset.used==='1';
  if(used){c.classList.add('used');
    const b=document.createElement('span');b.className='b used';b.textContent='已用';
    c.querySelector('.badges').appendChild(b);}
});
filt();
"""


def build(root: Path, out_html: Path, title: str = "图案库") -> Path:
    from . import library

    recs = library.load(root)
    cells = []
    for r in recs:
        # 图片相对 out_html 所在目录的路径
        img = library.img_path(root, r.batch, r.rel, r.file)
        try:
            rel_img = img.relative_to(out_html.parent)
            src = str(rel_img).replace("\\", "/")
        except ValueError:
            src = "file:///" + str(img).replace("\\", "/")
        if not img.exists():
            continue
        lab = r.label or ""
        badges = []
        if lab:
            badges.append(f'<span class="b {lab}">'
                          f'{"好" if lab=="good" else "坏" if lab=="bad" else "跳过"}'
                          f'</span>')
        if r.used:
            badges.append('<span class="b used">已用</span>')
        if r.pred >= 0:
            badges.append(f'<span class="b pred">预测 {r.pred*100:.0f}</span>')
        cap = (r.title or r.file)[:64]
        cells.append(
            f'<div class="cell {lab}" data-key="{H.escape(r.key())}" '
            f'data-batch="{H.escape(r.batch)}" data-rel="{H.escape(r.rel)}" '
            f'data-file="{H.escape(r.file)}" data-label="{lab}" '
            f'data-kw="{H.escape(r.keyword)}" data-title="{H.escape(cap)}" '
            f'data-used="{1 if r.used else 0}">'
            f'<img loading="lazy" src="{H.escape(src)}" alt="">'
            f'<div class="badges">{"".join(badges)}</div>'
            f'<div class="cap">{H.escape(cap)}<div class="note">'
            f'{H.escape(r.note or "")}</div></div>'
            f'<div class="acts">'
            f'<button onclick="toggleUsed(\'{H.escape(r.key())}\')">标记已用</button>'
            f'<button onclick="setNote(\'{H.escape(r.key())}\')">备注</button>'
            f'</div></div>')

    st = library.stats(root)
    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<header>
 <h1>{title}</h1>
 <div class="stats">
  <span class="pill">共 <b>{st['total']}</b></span>
  <span class="pill good">好 <b>{st['good']}</b></span>
  <span class="pill bad">坏 <b>{st['bad']}</b></span>
  <span class="pill skip">跳过 <b>{st['skip']}</b></span>
  <span class="pill">未标 <b>{st['unlabeled']}</b></span>
  <span class="pill used">已用 <b>{st['used']}</b></span>
  <span class="pill">显示中 <b id="shown">{st['total']}</b></span>
 </div>
 <div class="bar">
  <select id="f-label">
   <option value="good">只看「好」</option>
   <option value="">全部</option>
   <option value="un">未标记</option>
   <option value="bad">只看「坏」</option>
   <option value="skip">只看「跳过」</option>
  </select>
  <select id="f-used">
   <option value="">已用状态不限</option>
   <option value="no">未用过</option>
   <option value="yes">已用过</option>
  </select>
  <input id="f-kw" placeholder="关键词（如 maple / ghost）">
  <input id="f-text" placeholder="标题或备注里搜">
  <button onclick="dump()" class="pri">导出变更</button>
  <button onclick="copyKeys()">复制当前结果文件名</button>
 </div>
</header>
<main>
 <div class="grid" id="grid">{''.join(cells)}</div>
 {'' if cells else '<div class="empty">库里还没有图。先跑 patterncrawl 爬一批。</div>'}
</main>
<div id="out">
 <h2 style="margin:0 0 8px">变更（共 <span id="nchg">0</span> 条）</h2>
 <p style="color:#8b93a1">把下面的内容复制或存成 json 发我，我写回图案库。</p>
 <textarea id="jsonout" readonly></textarea>
 <div style="margin-top:12px;display:flex;gap:9px">
  <button class="pri" onclick="copyOut()">复制</button>
  <button onclick="dl()">下载 json</button>
  <button onclick="document.getElementById('out').style.display='none'">关闭</button>
 </div>
</div>
<script>{JS}</script></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")
    return out_html
