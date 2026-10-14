# -*- coding: utf-8 -*-
"""
shein_scout.picker —— 图片点选页（替代标签点亮）

按用户的判断重做：
  * 核心是**看到图**，不是看标签词表
  * 你点好看的图，就是偏好信号
  * 不做逐张"好/坏"标注（100 张要点 100 次太累）
  * 排序：用你点过的图算特征相似度（后续可换成学到的模型）

交互：
  单击图片 = 选中（我喜欢）
  Shift+单击 = 排除（明确不要）
  顶部实时显示选中数
  导出 = 我喜欢的图片列表
"""
from __future__ import annotations

import html as H
import json
from pathlib import Path

CSS = """
:root{--bg:#0e1013;--card:#161a20;--line:#252b35;--fg:#e9ecf1;--dim:#8b93a1;
--yes:#4ade80;--no:#f87171;--acc:#60a5fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{position:sticky;top:0;z-index:20;background:rgba(14,16,19,.97);
border-bottom:1px solid var(--line);padding:12px 18px;backdrop-filter:blur(8px)}
h1{margin:0 0 6px;font-size:17px}
.sub{color:var(--dim);font-size:12.5px}
.sub b{color:#fff}
.bar{margin-top:9px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
button{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:7px 13px;border-radius:8px;cursor:pointer;font-size:13px;font-family:inherit}
button:hover{background:#1d232d}
button.pri{background:#1d3557;border-color:#2c4a75}
select{background:var(--card);border:1px solid var(--line);color:var(--fg);
padding:7px 11px;border-radius:8px;font-size:13px}
main{padding:14px 16px 70px}
.grid{display:grid;gap:11px;grid-template-columns:repeat(auto-fill,minmax(178px,1fr))}
.c{position:relative;border:3px solid transparent;border-radius:10px;
overflow:hidden;cursor:pointer;background:var(--card);transition:transform .08s}
.c:hover{transform:scale(1.015)}
.c img{width:100%;aspect-ratio:1;object-fit:cover;display:block;background:#fff}
.c.yes{border-color:var(--yes)}
.c.no{border-color:var(--no);opacity:.34}
.c .rank{position:absolute;top:5px;left:5px;background:rgba(0,0,0,.75);
color:#fff;font-size:11px;padding:1px 7px;border-radius:6px}
.c .mark{position:absolute;top:5px;right:5px;width:24px;height:24px;
border-radius:50%;display:none;align-items:center;justify-content:center;
font-size:15px;font-weight:700}
.c.yes .mark{display:flex;background:var(--yes);color:#04220f}
.c.no .mark{display:flex;background:var(--no);color:#2a0707}
.c.wm{opacity:.45}
.c.wm::after{content:"⚠ 疑似不可用";position:absolute;bottom:5px;left:5px;
background:rgba(220,60,60,.92);color:#fff;font-size:10.5px;padding:1px 6px;
border-radius:5px}
.c .why{display:none;position:absolute;top:5px;right:5px;background:rgba(0,0,0,.8);
color:#fca5a5;font-size:10.5px;padding:1px 7px;border-radius:5px;max-width:88%}
.c.wm .why{display:block}
.c.wm .mark{right:auto;left:5px;top:28px}
#out{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;z-index:99;
padding:40px;overflow:auto}
#out textarea{width:100%;height:46vh;background:#12151a;color:var(--fg);
border:1px solid var(--line);border-radius:9px;padding:13px;font:12.5px monospace}
.tip{color:var(--dim);font-size:12.5px;margin-top:7px}
kbd{background:#1c222c;border:1px solid var(--line);border-radius:5px;
padding:1px 6px;font-size:11.5px}
"""

JS = """
const KEY='picker_'+BATCHKEY;
let sel={};
try{sel=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){sel={};}
const cells=[...document.querySelectorAll('.c')];

function paint(){
  let ny=0,nn=0;
  cells.forEach(c=>{
    const k=c.dataset.key,v=sel[k]||'';
    c.classList.remove('yes','no');
    if(v==='yes'){c.classList.add('yes');ny++;}
    if(v==='no'){c.classList.add('no');nn++;}
    c.querySelector('.mark').textContent=v==='yes'?'✓':(v==='no'?'✕':'');
  });
  document.getElementById('ny').textContent=ny;
  document.getElementById('nn').textContent=nn;
  localStorage.setItem(KEY,JSON.stringify(sel));
}
cells.forEach(c=>{
  c.addEventListener('click',e=>{
    const k=c.dataset.key;
    const want=e.shiftKey?'no':'yes';
    sel[k]=(sel[k]===want)?'':want;   // 再点一次取消
    paint();
  });
});
function filt(){
  const s=document.getElementById('f').value;
  cells.forEach(c=>{
    const v=sel[c.dataset.key]||'';
    let show=true;
    if(s==='yes')show=(v==='yes');
    else if(s==='no')show=(v==='no');
    else if(s==='un')show=(!v);
    c.style.display=show?'block':'none';
  });
}
document.getElementById('f').addEventListener('change',filt);
const hw=document.getElementById('hidewm');
function applyWm(){
  cells.forEach(c=>{
    if(hw.checked && c.dataset.wm==='1') c.style.display='none';
  });
}
hw.addEventListener('change',()=>{if(hw.checked)applyWm();else filt();});
function dump(){
  const yes=[],no=[];
  cells.forEach(c=>{
    const v=sel[c.dataset.key];
    if(v==='yes')yes.push({batch:c.dataset.batch,rel:c.dataset.rel,
      file:c.dataset.file,keyword:c.dataset.kw,label:'good'});
    if(v==='no')no.push({batch:c.dataset.batch,rel:c.dataset.rel,
      file:c.dataset.file,keyword:c.dataset.kw,label:'bad'});
  });
  const payload={liked:yes,disliked:no};
  document.getElementById('jsonout').value=JSON.stringify(payload,null,1);
  document.getElementById('out').style.display='block';
}
function copyOut(){
  const t=document.getElementById('jsonout');t.select();document.execCommand('copy');
  alert('已复制。发我或存成 json，然后跑 patternlabel <文件>');
}
function dl(){
  const b=new Blob([document.getElementById('jsonout').value],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='picked.json';a.click();
}
function allYes(){
  if(!confirm('把当前筛选下所有图都标记为「喜欢」？'))return;
  cells.forEach(c=>{if(c.style.display!=='none')sel[c.dataset.key]='yes';});
  paint();
}
function clr(){if(confirm('清空所有标记？')){sel={};paint();}}
paint();filt();applyWm();
"""


# 图库/水印站点（先把 OCR 文字归一化成纯字母数字再匹配，
# 因为 OCR 读域名会有噪声：tuchong -> "tL℃hong" / "tuch*ng"）
WATERMARK_KEYS = (
    "tuchong", "shutterstock", "gettyimages", "istockphoto", "dreamstime",
    "depositphotos", "123rf", "stockadobe", "vecteezy", "freepik", "alamy",
    "pngtree", "51yuansu", "zcool", "huaban", "nipic", "58pic", "699pic",
    "lovepik", "ooopic", "vcgcom", "qiantu", "zhitu",
    # 中文站名（汉字不会被归一化掉）
    "图虫", "视觉中国", "千图", "千库", "包图", "昵图", "摄图", "觅知",
    "汇图", "我图", "搜图", "素材",
)

# 版权标识：图上有这些说明是受保护的图案，不能直接用于生产
COPYRIGHT_KEYS = (
    "copyright", "allrightsreserved", "allrights", "reserved",
    "llc", "ltd", "inc", "gmbh", "cojp",
    "loremipsum", "lorem", "placeholder","sampletext",
    "licensedby", "license", "trademark", "registered",
    "版权", "著作权", "版权所有", "仅供", "禁止",
)

# 明显的图库 ID 格式
ID_PATTERNS = (
    "imageid", "stockid", "素材编号", "编号", "id:",
)


def _norm(txt: str) -> str:
    """只保留字母数字和汉字，去掉 OCR 噪声符号。"""
    return "".join(ch for ch in (txt or "").lower()
                   if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def detect_watermark(path: Path) -> tuple[bool, str, str]:
    """检测水印/版权。返回 (是否问题图, 原因, 识别到的文字)"""
    try:
        from . import ocr
        res = ocr.read_text(path)
        txt = res if isinstance(res, str) else (res.get("text") or "")
        txt = (txt or "").strip()
    except Exception:
        return False, "", ""
    if not txt:
        return False, "", ""
    norm = _norm(txt)
    short = txt[:120]

    for k in WATERMARK_KEYS:
        if k in norm:
            return True, f"图库水印({k})", short
    for k in COPYRIGHT_KEYS:
        if k in norm:
            return True, f"版权标识({k})", short
    for k in ID_PATTERNS:
        if k in norm:
            return True, f"图库编号({k})", short
    # 图上出现网址（归一化后含 www + com/cn/net）
    if "www" in norm and any(t in norm for t in ("com", "cn", "net")):
        return True, "图上有网址", short
    return False, "", short


def build(root: Path, out_html: Path, recs: list | None = None,
          title: str = "点选你喜欢的图案", only_batch: str = "",
          max_n: int = 0, rank: bool = False) -> Path:
    """生成图片点选页。

    recs 为空则用整个图案库；rank=True 时按已学偏好排序（好的排前面）。
    """
    from . import library, preflearn

    if recs is None:
        recs = library.load(root)
        if only_batch:
            recs = [r for r in recs if r.batch == only_batch]
    # 只保留图片真实存在的
    items = []
    for r in recs:
        p = library.img_path(root, r.batch, r.rel, r.file)
        if p.exists():
            items.append((r, p))
    if rank:
        model = preflearn.load_model(root)
        if model:
            pairs = dict(preflearn.rank_batch(root, [r for r, _ in items], model))
            items.sort(key=lambda t: -pairs.get(t[0].key(), 0.5))
    if max_n:
        items = items[:max_n]

    # 把图片复制成短名放到页面旁（img/0001.jpg）。
    # 为什么这么做：
    #   1) 相对路径简单到不可能出错（不再依赖 ../ 层数）
    #   2) 避开中文/空格路径在浏览器里的兼容问题
    #   3) 页面体积小、加载快
    import shutil

    img_dir = out_html.parent / "img"
    img_dir.mkdir(parents=True, exist_ok=True)
    cells = []
    n_copy_fail = 0
    n_wm = 0
    wm_cache_p = out_html.parent / "watermark.json"
    try:
        import json as _json
        wm_cache = _json.loads(wm_cache_p.read_text(encoding="utf-8")) \
            if wm_cache_p.exists() else {}
    except Exception:
        wm_cache = {}
    for i, (r, p) in enumerate(items, 1):
        ext = p.suffix.lower() or ".jpg"
        short = f"{i:04d}{ext}"
        dst = img_dir / short
        try:
            if not dst.exists():
                shutil.copy2(p, dst)
        except Exception:
            n_copy_fail += 1
            continue
        # 水印检测（按路径缓存，不必每次重跑 OCR）
        wm_key = r.key()
        if wm_key not in wm_cache:
            hit, why, txt = detect_watermark(p)
            wm_cache[wm_key] = {"wm": hit, "why": why, "txt": txt}
        wm = bool(wm_cache[wm_key].get("wm"))
        why = wm_cache[wm_key].get("why", "")
        if wm:
            n_wm += 1
        wm_attr = (' data-wm="1" data-why="' + H.escape(why) + '"') if wm else ""
        lab = r.label or ""
        cls = "c yes" if lab == "good" else ("c no" if lab == "bad" else "c")
        if wm:
            cls += " wm"
        cells.append(
            f'<div class="{cls}"{wm_attr} data-key="{H.escape(r.key())}" '
            f'data-batch="{H.escape(r.batch)}" data-rel="{H.escape(r.rel)}" '
            f'data-file="{H.escape(r.file)}" data-kw="{H.escape(r.keyword)}">'
            f'<span class="rank">#{i}</span>'
            + (f'<span class="why">{H.escape(why)}</span>' if wm else '')
            + f'<span class="mark"></span>'
            f'<img loading="lazy" src="img/{short}" alt=""></div>')

    try:
        import json as _json
        wm_cache_p.write_text(_json.dumps(wm_cache, ensure_ascii=False),
                              encoding="utf-8")
    except Exception:
        pass

    n_yes = sum(1 for r, _ in items if r.label == "good")
    n_no = sum(1 for r, _ in items if r.label == "bad")
    st = library.stats(root)

    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<header>
 <h1>{title}</h1>
 <div class="sub">
  <b>{len(items)}</b> 张图 ·
  已选 <b id="ny" style="color:#4ade80">{n_yes}</b> ·
  排除 <b id="nn" style="color:#f87171">{n_no}</b> ·
  图案库共 {st['total']} 张（已选过 {st['good']}）
  {'· <b style="color:#f87171">' + str(n_wm) + ' 张带水印已默认隐藏</b>' if n_wm else ''}
 </div>
 <div class="bar">
  <select id="f">
   <option value="">显示全部</option>
   <option value="yes">只看已选</option>
   <option value="no">只看排除</option>
   <option value="un">只看没标的</option>
  </select>
  <label style="color:#8b93a1;font-size:12.5px;display:flex;align-items:center;gap:5px">
   <input type="checkbox" id="hidewm" checked> 隐藏疑似不可用（水印/版权）
  </label>
  <button onclick="allYes()">筛选下全选</button>
  <button class="pri" onclick="dump()">导出选择</button>
  <button onclick="clr()">清空</button>
 </div>
 <div class="tip">
  <kbd>单击</kbd> 选中（喜欢） &nbsp;
  <kbd>Shift+单击</kbd> 排除（明确不要） &nbsp;
  再点一次取消 &nbsp;·&nbsp; 选择自动存在浏览器里
 </div>
</header>
<main><div class="grid">{''.join(cells)}</div></main>
<div id="out">
 <h2 style="margin:0 0 8px">你的选择</h2>
 <p style="color:#8b93a1">复制或存成 json，然后跑
  <code>python -m shein_scout patternlabel &lt;文件&gt;</code></p>
 <textarea id="jsonout" readonly></textarea>
 <div style="margin-top:12px;display:flex;gap:9px">
  <button class="pri" onclick="copyOut()">复制</button>
  <button onclick="dl()">下载 json</button>
  <button onclick="document.getElementById('out').style.display='none'">关闭</button>
 </div>
</div>
<script>const BATCHKEY={json.dumps(only_batch or "all")};
let sel={json.dumps({r.key(): ("yes" if r.label == "good" else "no")
                     for r, _ in items if r.label in ("good", "bad")})};{JS}</script>
</body></html>"""
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(doc, encoding="utf-8")
    return out_html
