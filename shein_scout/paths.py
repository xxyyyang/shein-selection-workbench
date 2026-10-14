# -*- coding: utf-8 -*-
"""
shein_scout/paths.py —— 统一的路径解析（迁移的关键）

设计原则：
  1) **一个根**：所有路径都从"项目根"和"数据根"推导
  2) **可覆盖**：环境变量 > 配置文件 > 默认值
  3) **能自检**：提供 hardcoded_report() 扫出还写死路径的地方

用法：
    from .paths import ROOT, DATA_DIR, ARCHIVE, PICKED, VOCAB_DIR, cfg
"""
from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------- 项目根
# shein_scout/ 的上一级。整个项目挪到哪都能自动找到自己。
ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 数据根
# 图片、标签、标题等"用户数据"放哪。
# 优先级：环境变量 DSH_SCOUT_DATA > <根>/../选品 > <根>/data
def _resolve_data_dir() -> Path:
    env = os.environ.get("DSH_SCOUT_DATA", "").strip()
    if env:
        return Path(env)
    # 依次尝试：项目同级的「选品」目录 → 项目内 data/
    for cand in (ROOT.parent / "选品", ROOT / "data"):
        try:
            if cand.exists():
                return cand
        except Exception:
            pass
    return ROOT / "data"


DATA_DIR = _resolve_data_dir()

# ---------------------------------------------------------------- 配置
CFG_FILE = DATA_DIR / "配置.json"


def load_cfg() -> dict:
    """读配置，带默认值。文件不存在就创建一个。"""
    d = {}
    if CFG_FILE.exists():
        try:
            d = json.loads(CFG_FILE.read_text(encoding="utf-8"))
        except Exception:
            d = {}
    # 默认值
    d.setdefault("数据目录", str(DATA_DIR))
    d.setdefault("项目目录", str(ROOT))
    d.setdefault("Python", os.environ.get("DSH_PYTHON", ""))
    d.setdefault("ARK_API_KEY", os.environ.get("ARK_API_KEY", ""))
    d.setdefault("ARK_MODEL", os.environ.get("ARK_MODEL", "doubao-seed-evolving"))
    d.setdefault("海运", 45)
    d.setdefault("空运", 20)
    d.setdefault("市场选择", "全部")
    d.setdefault("节日", "万圣节")
    d.setdefault("关键词", "halloween pattern")
    d.setdefault("数量", 60)
    d.setdefault("SHEIN词库目录",
                 os.environ.get("DSH_SHEIN_VOCAB", str(DATA_DIR / "SHEIN词库")))
    return d


def save_cfg(cfg: dict) -> None:
    CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CFG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8")


cfg = load_cfg()

# ---------------------------------------------------------------- 内容路径
# 归拢后的结构：所有数据都在 数据根 下。
# 兼容旧结构：读不到就用项目目录里的老位置。

def _first(*cands):
    """返回第一个存在的路径；都不存在时返回第一个。"""
    for c in cands:
        try:
            if c and Path(c).exists():
                return Path(c)
        except Exception:
            pass
    return Path(cands[0]) if cands else None


# ---- 图案库 ----
IMG_ROOT = _first(                        # 图的实体
    DATA_DIR / "图案库" / "图",
    ROOT / "patterns",                    # 旧位置
)
LIBJSONL = _first(
    DATA_DIR / "图案库" / "library.jsonl",
    ROOT / "patterns" / "library.jsonl",
)
TAGDATA = _first(
    DATA_DIR / "图案库" / "标签.json",
    ROOT / "work" / "controlled_tags.json",
)
VOCAB = _first(
    DATA_DIR / "图案库" / "词表.json",
    ROOT / "work" / "标签词表.json",
)
TITLES = _first(
    DATA_DIR / "图案库" / "标题.json",
    ROOT / "work" / "titles.json",
)
PREFS = _first(
    DATA_DIR / "图案库" / "偏好",
    ROOT / "work" / "preference",
)

# 写回时**必须和读取来源一致**，否则会路径分叉（CLI 写旧位置、GUI 读新位置）
TAGDATA_W = TAGDATA
VOCAB_W = VOCAB
TITLES_W = TITLES
LIBJSONL_W = LIBJSONL

# 存档与已选
ARCHIVE = Path(cfg.get("存档目录") or (DATA_DIR / "存档"))
PICKED = Path(cfg.get("已选目录") or (DATA_DIR / "已选"))
RECYCLE = Path(cfg.get("回收站目录") or (DATA_DIR / "回收站"))
LOGS = Path(cfg.get("日志目录") or (DATA_DIR / "日志"))
THUMBS = ARCHIVE / "_缩略图"

# SHEIN 词库
# 优先级：配置里的「SHEIN词库目录」 > 环境变量 DSH_SHEIN_VOCAB > <数据根>/SHEIN词库
VOCAB_DIR = _first(
    cfg.get("SHEIN词库目录") or "",
    os.environ.get("DSH_SHEIN_VOCAB", "").strip(),
    DATA_DIR / "SHEIN词库",
)

# 用户文件
TAGSETS = Path(cfg.get("标签集文件") or (DATA_DIR / "我的标签集.json"))
USEDLIST = Path(cfg.get("已用清单") or (DATA_DIR / "已用清单.txt"))

# Python 解释器
PYTHON = cfg.get("Python") or os.environ.get("DSH_PYTHON", "") or ""


def describe() -> str:
    """打印当前路径解析结果（自检/init 用）。"""
    def ok(p):
        try:
            return "✅" if Path(p).exists() else "❌"
        except Exception:
            return "?"
    rows = [
        ("项目根", ROOT), ("数据根", DATA_DIR), ("配置文件", CFG_FILE),
        ("Python", PYTHON), ("图案库索引", LIBJSONL), ("标签数据", TAGDATA),
        ("受控词表", VOCAB), ("标题", TITLES), ("存档", ARCHIVE),
        ("已选", PICKED), ("SHEIN词库", VOCAB_DIR),
    ]
    lines = []
    for name, v in rows:
        lines.append(f"  {name:10s} {ok(v)}  {v}")
    return chr(10).join(lines)


# ---------------------------------------------------------------- 首次运行
def ensure_layout(verbose: bool = False) -> dict:
    """建好所有需要的目录和空的数据文件（**首次运行/迁移后自动调用**）。

    用户要的效果：迁移时只拷数据，程序打开后自动补全目录结构。

    返回 {创建了什么}。
    """
    made = {"dirs": [], "files": [], "skipped": []}

    # ---- 目录 ----
    DIRS = [
        DATA_DIR,
        DATA_DIR / "图案库",
        DATA_DIR / "图案库" / "图",
        DATA_DIR / "图案库" / "偏好",
        DATA_DIR / "SHEIN词库",
        ARCHIVE,
        PICKED,
        RECYCLE,
        LOGS,
        THUMBS,
        ROOT / "patterns",
        ROOT / "work",
        ROOT / "work" / "preference",
    ]
    for d in DIRS:
        try:
            if not Path(d).exists():
                Path(d).mkdir(parents=True, exist_ok=True)
                made["dirs"].append(str(d))
        except Exception as e:
            made["skipped"].append(f"{d} ({type(e).__name__})")

    # ---- 空数据文件（不存在才建，绝不覆盖）----
    FILES = [
        (LIBJSONL_W, ""),                     # jsonl 空文件
        (TAGDATA_W, "{}"),                    # json 空对象
        (TITLES_W, "{}"),
        (TAGSETS, "{}"),
        (USEDLIST, ""),
    ]
    for f, init in FILES:
        try:
            if not Path(f).exists():
                Path(f).parent.mkdir(parents=True, exist_ok=True)
                Path(f).write_text(init, encoding="utf-8")
                made["files"].append(str(f))
        except Exception as e:
            made["skipped"].append(f"{f} ({type(e).__name__})")

    # 词表（没词表就没法打标签，给个最小可用的）
    if not Path(VOCAB).exists():
        try:
            Path(VOCAB).write_text(
                '{"_说明":"受控标签词表，缺失时自动建的空表。'
                '请把完整的词表拷到 图案库/词表.json", "维度": []}',
                encoding="utf-8")
            made["files"].append(str(VOCAB))
        except Exception:
            pass

    # 配置（第一次运行会写一份默认的）
    if not CFG_FILE.exists():
        try:
            save_cfg(cfg)
            made["files"].append(str(CFG_FILE))
        except Exception:
            pass

    if verbose:
        if made["dirs"]:
            print(f"  新建目录 {len(made['dirs'])} 个:")
            for d in made["dirs"][:12]:
                print(f"     + {d}")
        if made["files"]:
            print(f"  新建文件 {len(made['files'])} 个:")
            for f in made["files"][:12]:
                print(f"     + {f}")
        if not made["dirs"] and not made["files"]:
            print("  ✓ 目录结构已完整，无需创建")
    return made


# ---------------------------------------------------------------- 防回归
# 允许出现硬编码路径的文件（文档字符串/注释里提到路径是正常的）
_SCAN_SKIP = {"paths.py"}


def hardcoded_report(verbose: bool = False) -> dict:
    """扫 shein_scout/ 里还有哪些硬编码绝对路径。

    只报**代码行**（不含注释、文档字符串里提到路径的情况大致靠启发式排除）。
    返回 {文件名: [行号, ...]}。
    """
    import re
    out: dict = {}
    pat = re.compile(r'["\']((?:[A-Za-z]:\\\\|[A-Za-z]:/)|/(?:Users|home)/)')
    for p in sorted(ROOT.joinpath("shein_scout").glob("*.py")):
        if p.name in _SCAN_SKIP:
            continue
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        hits = []
        for i, ln in enumerate(lines, 1):
            s = ln.strip()
            if s.startswith("#"):
                continue
            if pat.search(ln):
                hits.append((i, s[:88]))
        if hits:
            out[p.name] = hits
    if verbose:
        if not out:
            print("  ✅ 没有硬编码绝对路径")
        for f, hits in out.items():
            print(f"  ⚠️ {f}")
            for i, s in hits:
                print(f"       行{i}: {s}")
    return out
