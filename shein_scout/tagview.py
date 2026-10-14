# -*- coding: utf-8 -*-
"""
shein_scout.tagview —— 图上显示 AI 标签 + 勾选标签去爬图

按用户要求做的**单一流程**：
    爬图 -> AI 打标签 -> 图上显示标签 -> 你勾选认可的标签
    -> 用勾选的标签当关键词，再去爬

与之前的区别：
  * 标签**直接显示在每张图上**（不是单独的标签页，看不到图）
  * 勾选 = 你对"这张图的标签对不对"的确认，也是你的审美沉淀
  * 勾完直接出**抓图关键词和命令**，不是拿去排序
"""
from __future__ import annotations

import html as H
import json
import shutil
from pathlib import Path

CSS = """
:root{--bg:#0e1013;--card:#171b21;--line:#262c36;--fg:#e9ecf1;--dim:#8b93a1;
--sel:#4ade80;--acc:#60a5fa;--tag:#2b3440;--tagsel:#1f4d31}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:20;background:rgba(14,16,19,.97);
border-bottom:1px solid var(--line);padding:11px 18px;backdrop-filter:blur(8px)}
h1{margin:0 0 6px;font-size:16.5px}
.sub{color:var(--dim);font-size:12.5px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px;align-items:center}
button{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:6px 12px;border-radius:8px;cursor:pointer;font-size:13px;font-family:inherit}
button:hover{background:#1d232d}
button.pri{background:#1d3557;border-color:#2c4a75}
input,select{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:6px 10px;border-radius:8px;font-size:13px}
main{padding:14px 18px 80px}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.c{background:var(--card);border:2px solid var(--line);border-radius:11px;
overflow:hidden;display:flex;flex-direction:column}
.c.picked{border-color:var(--sel)}
.c img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#fff;
cursor:zoom-in}
.tags{padding:9px 10px;display:flex;flex-direction:column;gap:7px;flex:1}
.dim{display:flex;gap:5px;flex-wrap:wrap;align-items:center}
.dn{color:var(--dim);font-size:11px;min-width:52px;flex-shrink:0}
.t{background:var(--tag);border:1px solid var(--line);border-radius:14px;
padding:2px 9px;font-size:11.5px;cursor:pointer;user-select:none;
transition:all .1s;white-space:nowrap}
.t:hover{border-color:#3d4a5a}
.t.on{background:var(--tagsel);border-color:var(--sel);color:#d6ffe4;font-weight:600}
.t.no{background:#3a1f22;border-color:#7f3a3a;color:#ffc9c9;text-decoration:line-through}
.acts{display:flex;gap:6px;padding:0 10px 10px}
.acts button{flex:1;font-size:11.5px;padding:5px 3px}
#zoombox{position:fixed;inset:0;background:rgba(0,0,0,.93);display:none;
align-items:center;justify-content:center;z-index:99;cursor:zoom-out}
#zoombox img{max-width:94vw;max-height:94vh;object-fit:contain}
#out{position:fixed;inset:0;background:rgba(0,0,0,.93);display:none;z-index:100;
padding:36px;overflow:auto}
#out textarea{width:100%;height:44vh;background:#12151a;color:var(--fg);
border:1px solid var(--line);border-radius:9px;padding:13px;font:12.5px monospace}
kbd{background:#1c222c;border:1px solid var(--line);border-radius:5px;
padding:1px 6px;font-size:11.5px}
"""

JS = """
const KEY='tagview_'+BATCHKEY;
let state={};   // {key: {pick:bool, tags:{dim:[...]}, removed:{dim:[...]}}}
try{state=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){state={};}
const cards=[...document.querySelectorAll('.c')];

function ensure(k){ if(!state[k]) state[k]={pick:false,tags:{},removed:{}}; return state[k]; }
function save(){ localStorage.setItem(KEY,JSON.stringify(state)); }

cards.forEach(c=>{
  const k=c.dataset.key;
  c.querySelectorAll('.t').forEach(t=>{
    t.addEventListener('click',e=>{
      e.stopPropagation();
      const s=ensure(k), d=t.dataset.dim, v=t.dataset.tag;
      if(e.shiftKey){
        // Shift+点击 = 否定这个标签
        s.removed[d]=s.removed[d]||[];
        if(s.removed[d].includes(v)) s.removed[d]=s.removed[d].filter(x=>x!==v);
        else { s.removed[d].push(v);
               s.tags[d]=(s.tags[d]||[]).filter(x=>x!==v); }
      } else {
        s.tags[d]=s.tags[d]||[];
        if(s.tags[d].includes(v)) s.tags[d]=s.tags[d].filter(x=>x!==v);
        else s.tags[d].push(v);
        s.removed[d]=(s.removed[d]||[]).filter(x=>x!==v);
      }
      paint(c); save(); summary();
    });
  });
  c.querySelector('img').addEventListener('click',()=>zoom(c));
});
function paint(c){
  const k=c.dataset.key, s=ensure(k);
  c.querySelectorAll('.t').forEach(t=>{
    const d=t.dataset.dim, v=t.dataset.tag;
    t.classList.remove('on','no');
    if((s.tags[d]||[]).includes(v)) t.classList.add('on');
    if((s.removed[d]||[]).includes(v)) t.classList.add('no');
  });
  const n=Object.values(s.tags).reduce((a,x)=>a+x.length,0);
  c.classList.toggle('picked', s.pick || n>0);
  c.querySelector('.cnt').textContent = s.pick? '已选' : (n? n+' 个标签':'');
}
function zoom(c){
  const z=document.getElementById('zoombox');
  z.querySelector('img').src=c.querySelector('img').src;
  z.style.display='flex';
}
document.getElementById('zoombox').onclick=()=>{
  document.getElementById('zoombox').style.display='none';};

function pickAll(){ cards.forEach(c=>{ ensure(c.dataset.key).pick=true; paint(c); }); save(); summary(); }
function clearAll(){ if(confirm('清空所有勾选和标签调整？')){ state={}; cards.forEach(paint); save(); summary(); } }

function summary(){
  let np=0, nt=0;
  const kw=new Set();
  cards.forEach(c=>{
    const s=state[c.dataset.key]; if(!s) return;
    if(s.pick) np++;
    Object.values(s.tags).forEach(arr=>{ nt+=arr.length; });
  });
  document.getElementById('np').textContent=np;
  document.getElementById('nt').textContent=nt;
  // 汇总所有被认可的标签
  const tally={};
  cards.forEach(c=>{
    const s=state[c.dataset.key]; if(!s) return;
    Object.entries(s.tags).forEach(([d,arr])=>arr.forEach(v=>{
      const key=d+'|'+v; tally[key]=(tally[key]||0)+1;
    }));
  });
  const top=Object.entries(tally).sort((a,b)=>b[1]-a[1]);
  const box=document.getElementById('top');
  if(!top.length){ box.textContent='（还没勾选任何标签）'; return; }
  box.innerHTML=top.slice(0,26).map(([k,n])=>{
    const [d,v]=k.split('|');
    return `<span class="t on" style="cursor:default">${v}<span style="color:#9fe8b8"> ${n}</span></span>`;
  }).join(' ');
}

function dump(){
  const picked=[], tagTally={}, removedTally={};
  cards.forEach(c=>{
    const k=c.dataset.key, s=state[k]; if(!s) return;
    const has = s.pick || Object.values(s.tags).some(a=>a.length);
    if(has) picked.push({key:k, picked:!!s.pick, tags:s.tags});
    Object.entries(s.tags).forEach(([d,arr])=>arr.forEach(v=>{
      tagTally[d+'|'+v]=(tagTally[d+'|'+v]||0)+1; }));
    Object.entries(s.removed||{}).forEach(([d,arr])=>arr.forEach(v=>{
      removedTally[d+'|'+v]=(removedTally[d+'|'+v]||0)+1; }));
  });
  const out={images:picked, 认可标签:tagTally, 否定标签:removedTally};
  document.getElementById('jsonout').value=JSON.stringify(out,null,1);
  document.getElementById('out').style.display='block';
}
function copyOut(){
  const t=document.getElementById('jsonout'); t.select(); document.execCommand('copy');
  alert('已复制。可以发我，也可以保存成文件。');
}
function dl(){
  const b=new Blob([document.getElementById('jsonout').value],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(b);
  a.download='tag_review.json'; a.click();
}
// 生成抓图命令（用认可最多的标签对应的英文关键词）
function genCmd(){
  const tally={};
  cards.forEach(c=>{
    const s=state[c.dataset.key]; if(!s) return;
    Object.entries(s.tags).forEach(([d,arr])=>arr.forEach(v=>{
      const key=d+'|'+v; tally[key]=(tally[key]||0)+1; }));
  });
  const top=Object.entries(tally).sort((a,b)=>b[1]-a[1]).slice(0,5)
    .map(([k])=>k.split('|')[1]);
  const kwmap = window.KW || {};
  const kws=[];
  top.forEach(t=>{ (kwmap[t]||[]).forEach(k=>{ if(!kws.includes(k)) kws.push(k); }); });
  const cmd = 'python -m shein_scout patterncrawl ' +
    kws.slice(0,5).map(k=>'"'+k+'"').join(' ') + ' --want 120';
  document.getElementById('jsonout').value = cmd;
  document.getElementById('out').style.display='block';
}
cards.forEach(paint); summary();
"""


def build(root: Path, out_html: Path, batch: str = "",
          title: str = "看图 · 确认标签") -> Path:
    """生成"图上带标签"的页面。"""
    libp = root / "patterns" / "library.jsonl"
    tagp = root / "work" / "controlled_tags.json"
    if not libp.exists():
        raise FileNotFoundError(f"图案库不存在: {libp}")
    recs = []
    for line in libp.read_text(encoding="utf-8").splitlines():
        if line.strip():
            recs.append(json.loads(line))
    tags = json.loads(tagp.read_text(encoding="utf-8")) if tagp.exists() else {}
    vocab = json.loads((root / "work" / "标签词表.json").read_text(encoding="utf-8"))
    if batch:
        recs = [r for r in recs if r.get("batch") == batch]

    # 关键词映射（给"生成抓图命令"用）
    kwmap = {}
    for d in vocab["维度"]:
        for t in d["标签"]:
            if t.get("关键词"):
                kwmap[t["名"]] = t["关键词"]

    def resolve(r):
        base = root / "patterns" / r.get("batch", "")
        rel = (r.get("rel") or r.get("file") or "").replace("\\", "/")
        parts = rel.split("/")
        while parts and parts[0] == r.get("batch"):
            parts = parts[1:]
        p = base.joinpath(*parts)
        return p if p.exists() else None

    # 图片复制成短名，路径不可能出错
    img_dir = out_html.parent / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    # 按节日分组（用户要求：看图确认标签要节日分类）
    def _fest_of(rr):
        kk = f"{rr.get('batch')}/{rr.get('rel') or rr.get('file')}"
        tt = tags.get(kk) or {}
        fs = tt.get("节日主题") or []
        return fs[0] if fs else "未分类"

    recs.sort(key=lambda rr: (_fest_of(rr), rr.get("batch", "")))

    _lastfest = [None]
    cells = []
    n_tagged = 0
    for i, r in enumerate(recs, 1):
        p = resolve(r)
        if not p:
            continue
        ext = p.suffix.lower() or ".jpg"
        short = f"{i:04d}{ext}"
        dst = img_dir / short
        try:
            if not dst.exists():
                shutil.copy2(p, dst)
        except Exception:
            continue
        key = f"{r.get('batch')}/{r.get('rel') or r.get('file')}"
        tg = tags.get(key)
        fnow = _fest_of(r)
        if fnow != _lastfest[0]:
            _lastfest[0] = fnow
            cells.append(
                f'<div style="grid-column:1/-1;margin:18px 0 4px;'
                f'border-left:4px solid #60a5fa;padding-left:10px;'
                f'font-size:16px;font-weight:600">{H.escape(fnow)}</div>')
        if tg:
            n_tagged += 1
        dims = []
        if tg:
            for d in vocab["维度"]:
                vals = tg.get(d["名"]) or []
                if not vals:
                    continue
                # 标签加 title，鼠标悬停能看解释
                desc = {t["名"]: (t.get("解释") or "") for t in d["标签"]}
                chips = "".join(
                    f'<span class="t" data-dim="{H.escape(d["名"])}" '
                    f'data-tag="{H.escape(v)}" '
                    f'title="{H.escape(desc.get(v) or v)}">'
                    f'{H.escape(v)}</span>'
                    for v in vals)
                dims.append(f'<div class="dim"><span class="dn">'
                            f'{H.escape(d["名"])}</span>{chips}</div>')
        else:
            dims.append('<div class="dim"><span class="dn">—</span>'
                        '<span style="color:#8b93a1;font-size:11.5px">'
                        '还没打标签</span></div>')
        cells.append(
            f'<div class="c" data-key="{H.escape(key)}">'
            f'<img loading="lazy" src="img/{short}" alt="">'
            f'<div class="tags">{"".join(dims)}</div>'
            f'<div class="acts">'
            f'<button onclick="event.stopPropagation();'
            f'ensure(this.closest(\'.c\').dataset.key).pick='
            f'!ensure(this.closest(\'.c\').dataset.key).pick;'
            f'paint(this.closest(\'.c\'));save();summary()">勾选这张</button>'
            f'<span class="cnt" style="color:#4ade80;font-size:11.5px;'
            f'align-self:center;min-width:52px"></span>'
            f'</div></div>')

    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{H.escape(title)}</title><style>{CSS}</style></head><body>
<header>
 <h1>{H.escape(title)}</h1>
 <div class="sub">
  共 <b>{len(cells)}</b> 张，其中 <b>{n_tagged}</b> 张已有 AI 标签 ·
  你勾选 <b id="np">0</b> 张 · 你认可的标签 <b id="nt">0</b> 个
 </div>
 <div class="bar">
  <button onclick="pickAll()">全部勾选</button>
  <button onclick="genCmd()" class="pri">生成抓图命令</button>
  <button onclick="dump()">导出结果</button>
  <button onclick="clearAll()">清空</button>
 </div>
 <div class="sub" style="margin-top:7px">
  <kbd>点标签</kbd> = 认可 &nbsp; <kbd>Shift+点标签</kbd> = 这张图不是这个标签 &nbsp;
  <kbd>点图</kbd> = 放大 &nbsp;·&nbsp; 「生成抓图命令」用你认可最多的标签去爬新图
 </div>
</header>
<main>
 <div style="background:#151a21;border:1px solid var(--line);
  border-left:3px solid var(--acc);padding:10px 13px;border-radius:8px;
  margin-bottom:14px;font-size:13px;color:#b6bec9">
  <b>你认可的标签汇总</b>（数字=多少张图被认定含此标签）
  <div id="top" style="margin-top:7px;display:flex;gap:6px;flex-wrap:wrap">
   （还没勾选任何标签）
  </div>
 </div>
 <div class="grid">{''.join(cells)}</div>
</main>
<div id="zoombox"><img alt=""></div>
<div id="out">
 <h2 style="margin:0 0 8px">结果</h2>
 <p style="color:#8b93a1">复制或下载。也可以直接用上面的「生成抓图命令」。</p>
 <textarea id="jsonout" readonly></textarea>
 <div style="margin-top:12px;display:flex;gap:9px">
  <button class="pri" onclick="copyOut()">复制</button>
  <button onclick="dl()">下载 json</button>
  <button onclick="document.getElementById('out').style.display='none'">关闭</button>
 </div>
</div>
<script>const BATCHKEY={json.dumps(batch or "all")};
window.KW={json.dumps(kwmap, ensure_ascii=False)};{JS}</script>
</body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")
    return out_html
