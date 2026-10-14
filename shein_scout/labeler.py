# -*- coding: utf-8 -*-
"""
shein_scout.labeler —— 打标签界面生成

要求（按用户场景设计）：
  * 一次标 100 张要快 —— 键盘操作，不用鼠标点
  * 标签自动存浏览器本地，防止误关丢失
  * 一键导出 JSON，贴回来我就能学

操作方式：
  ← → 或 A/D   上一张 / 下一张
  1 或 G       好（喜欢）
  2 或 B       坏（不喜欢）
  0 或 S       跳过（不确定）
  空格         放大看
  Enter        导出结果
"""
from __future__ import annotations

import json
from pathlib import Path

CSS = """
:root{--bg:#0e1013;--card:#161a20;--line:#252b35;--fg:#e9ecf1;--dim:#8b93a1;
--good:#4ade80;--bad:#f87171;--skip:#fbbf24;--acc:#60a5fa}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;overflow:hidden}
header{padding:11px 16px;border-bottom:1px solid var(--line);display:flex;
align-items:center;gap:14px;flex-wrap:wrap;background:#12151a}
h1{margin:0;font-size:15px;font-weight:600}
.pill{padding:3px 10px;border-radius:20px;background:#1c222c;font-size:12.5px;
border:1px solid var(--line)}
.pill b{color:var(--fg)}
.pill.good b{color:var(--good)} .pill.bad b{color:var(--bad)}
.pill.skip b{color:var(--skip)}
button{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:6px 13px;border-radius:8px;cursor:pointer;font-size:13px}
button:hover{background:#1d232d}
button.pri{background:#1d3557;border-color:#2c4a75}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));
gap:13px;padding:15px;overflow-y:auto;height:calc(100% - 63px)}
.cell{position:relative;background:var(--card);border:2px solid var(--line);
border-radius:11px;overflow:hidden;cursor:pointer;transition:border-color .12s}
.cell.good{border-color:var(--good)} .cell.bad{border-color:var(--bad)}
.cell.skip{border-color:var(--skip)}
.cell.cur{outline:3px solid var(--acc);outline-offset:1px}
.cell img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#fff}
.idx{position:absolute;top:6px;left:6px;background:rgba(0,0,0,.72);padding:1px 7px;
border-radius:6px;font-size:11.5px}
.tag{position:absolute;top:6px;right:6px;font-size:11.5px;padding:1px 8px;
border-radius:6px;font-weight:600}
.tag.good{background:var(--good);color:#04220f}
.tag.bad{background:var(--bad);color:#2a0707}
.tag.skip{background:var(--skip);color:#2a1e02}
.cap{padding:6px 8px;font-size:11.5px;color:var(--dim);height:3.2em;overflow:hidden}
#zoombox{position:fixed;inset:0;background:rgba(0,0,0,.93);display:none;
align-items:center;justify-content:center;z-index:99;cursor:zoom-out}
#zoombox img{max-width:94vw;max-height:94vh;object-fit:contain}
.help{color:var(--dim);font-size:12.5px;padding:0 16px 14px}
kbd{background:#1c222c;border:1px solid var(--line);border-radius:5px;
padding:1px 6px;font-size:11.5px;font-family:inherit}
#out{position:fixed;inset:0;background:rgba(0,0,0,.9);display:none;z-index:100;
padding:40px;overflow:auto}
#out textarea{width:100%;height:60vh;background:#12151a;color:var(--fg);
border:1px solid var(--line);border-radius:9px;padding:13px;font:12.5px monospace}
"""

JS = """
const KEY='shein_labels_'+BATCH;
let labels=JSON.parse(localStorage.getItem(KEY)||'{}');
let cur=0;
const cells=[...document.querySelectorAll('.cell')];

function paint(){
  cells.forEach((c,i)=>{
    const v=labels[c.dataset.file]||'';
    c.classList.remove('good','bad','skip');
    if(v)c.classList.add(v);
    c.classList.toggle('cur', i===cur);
    const t=c.querySelector('.tag');
    t.className='tag '+(v||'');
    t.textContent=v==='good'?'好':v==='bad'?'坏':v==='skip'?'跳过':'';
  });
  const n=Object.values(labels).filter(v=>v==='good').length;
  const b=Object.values(labels).filter(v=>v==='bad').length;
  const s=Object.values(labels).filter(v=>v==='skip').length;
  document.getElementById('ng').textContent=n;
  document.getElementById('nb').textContent=b;
  document.getElementById('ns').textContent=s;
  document.getElementById('nleft').textContent=cells.length-n-b-s;
  localStorage.setItem(KEY,JSON.stringify(labels));
}
function set(v){
  const f=cells[cur].dataset.file;
  labels[f]=(labels[f]===v)?'':v;
  paint();
  if(labels[f]&&cur<cells.length-1){cur++;paint();}
}
function go(d){
  cur=Math.max(0,Math.min(cells.length-1,cur+d));
  paint();
  cells[cur].scrollIntoView({block:'nearest',behavior:'smooth'});
}
document.addEventListener('keydown',e=>{
  const k=e.key.toLowerCase();
  if(k==='arrowright'||k==='d'){e.preventDefault();go(1)}
  else if(k==='arrowleft'||k==='a'){e.preventDefault();go(-1)}
  else if(k==='1'||k==='g'){e.preventDefault();set('good')}
  else if(k==='2'||k==='b'){e.preventDefault();set('bad')}
  else if(k==='0'||k==='s'){e.preventDefault();set('skip')}
  else if(k===' '){e.preventDefault();zoom()}
  else if(k==='enter'){e.preventDefault();exportJSON()}
});
function zoom(){
  const img=cells[cur].querySelector('img');
  const z=document.getElementById('zoombox');
  z.querySelector('img').src=img.src;
  z.style.display='flex';
}
document.getElementById('zoombox').onclick=()=>{
  document.getElementById('zoombox').style.display='none';};
cells.forEach((c,i)=>c.onclick=()=>{cur=i;paint();});
function exportJSON(){
  const out=[];
  cells.forEach(c=>{
    const v=labels[c.dataset.file];
    if(v)out.push({batch:BATCH,file:c.dataset.file,label:v,
                   keyword:c.dataset.kw||'',source:c.dataset.src||''});
  });
  const txt=JSON.stringify(out,null,1);
  document.getElementById('jsonout').value=txt;
  document.getElementById('out').style.display='block';
}
function copyOut(){
  const t=document.getElementById('jsonout');
  t.select();document.execCommand('copy');
  alert('已复制 '+t.value.split('"file"').length-1+' 条标签，粘贴给我即可');
}
function dl(){
  const blob=new Blob([document.getElementById('jsonout').value],
    {type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);a.download='labels_'+BATCH+'.json';a.click();
}
paint();
cells[0]&&cells[0].scrollIntoView({block:'nearest'});
"""


def build(manifest: list[dict], batch: str, out_html: Path,
          batch_dir: Path, title: str = "图案打标签") -> Path:
    """生成打标签页面。图片用相对路径引用本地文件。"""
    cells = []
    for m in manifest:
        f = m.get("file", "")
        # 优先用 manifest 里的 rel（唯一）；没有就回退到逐个目录找。
        # 踩过的坑：不同子目录里文件名都从 0001 开始，只靠文件名会指向错图。
        rel = m.get("rel") or ""
        if not rel or not (batch_dir / rel).exists():
            rel = ""
            for sub in sorted(batch_dir.iterdir()):
                if sub.is_dir() and (sub / f).exists():
                    rel = f"{sub.name}/{f}"
                    break
        if not rel:
            continue
        cells.append(
            f'<div class="cell" data-file="{f}" data-kw="{m.get("keyword","")}" '
            f'data-src="{m.get("source","")}" title="{m.get("title","")[:120]}">'
            f'<span class="idx">{m.get("idx",0)}</span><span class="tag"></span>'
            f'<img loading="lazy" src="{rel}" alt="">'
            f'<div class="cap">{m.get("title","")[:70]}</div></div>')

    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<header>
 <h1>{title}</h1>
 <span class="pill">批次 <b>{batch}</b></span>
 <span class="pill good">好 <b id="ng">0</b></span>
 <span class="pill bad">坏 <b id="nb">0</b></span>
 <span class="pill skip">跳过 <b id="ns">0</b></span>
 <span class="pill">未标 <b id="nleft">{len(cells)}</b></span>
 <button onclick="exportJSON()" class="pri">导出标签 (Enter)</button>
 <button onclick="localStorage.removeItem(KEY);location.reload()">清空重来</button>
</header>
<div class="help">
 <kbd>1</kbd>/<kbd>G</kbd> 好 &nbsp; <kbd>2</kbd>/<kbd>B</kbd> 坏 &nbsp;
 <kbd>0</kbd>/<kbd>S</kbd> 跳过 &nbsp; <kbd>←</kbd> <kbd>→</kbd> 切换 &nbsp;
 <kbd>空格</kbd> 放大 &nbsp; <kbd>Enter</kbd> 导出
 &nbsp;·&nbsp; 标签自动存在浏览器里，关掉再打开还在
</div>
<main>{''.join(cells)}</main>
<div id="zoombox"><img alt=""></div>
<div id="out">
 <h2 style="margin:0 0 10px">导出结果</h2>
 <p style="color:#8b93a1">把下面的内容复制给我，我就能开始学你的审美。
 也可以点「下载文件」存成 json 再发我。</p>
 <textarea id="jsonout" readonly></textarea>
 <div style="margin-top:12px;display:flex;gap:9px">
  <button class="pri" onclick="copyOut()">复制</button>
  <button onclick="dl()">下载文件</button>
  <button onclick="document.getElementById('out').style.display='none'">关闭</button>
 </div>
</div>
<script>const BATCH={json.dumps(batch)};{JS}</script></body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")
    return out_html
