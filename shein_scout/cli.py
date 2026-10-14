# -*- coding: utf-8 -*-
"""
shein_scout.cli —— 命令行入口

    python -m shein_scout doctor                          体检：环境 + 图源 + 样本
    python -m shein_scout crawl "午休毯 白底" -n 60        收集网络候选图
    python -m shein_scout build                           学口径：从过审/被刷样本建画像与模型
    python -m shein_scout score <图片或目录...>            打分排序，出 HTML + CSV
    python -m shein_scout run [--crawl "关键词"]           一条龙：抓图 -> 学口径 -> 打分 -> 报告
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import io_utils
from . import features, io_utils, learn, report, score as scoring

ROOT = Path.cwd()
CONFIG_NAME = "config.json"
DEFAULT_CONFIG = {
    "approved_dirs": ["samples/过审"],
    "rejected_dirs": ["samples/被刷"],
    "reference_dirs": ["samples/参考"],
    "label_approved": "过审",
    "label_rejected": "被刷",
    "note": "把图丢进 samples/过审 和 samples/被刷，再跑 python -m shein_scout build",
    "rules": {},
    "model": {"l2": 1.2, "iters": 2500, "lr": 0.6},
}


# --------------------------------------------------------------------------
def load_config(root: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    fp = root / CONFIG_NAME
    if fp.exists():
        try:
            user = json.loads(fp.read_text(encoding="utf-8"))
            for k, v in user.items():
                if k == "rules" and isinstance(v, dict):
                    cfg["rules"] = {**DEFAULT_CONFIG.get("rules", {}), **v}
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"!! {CONFIG_NAME} 读取失败（用默认配置继续）：{e}")
    return cfg


def ensure_config(root: Path) -> Path:
    fp = root / CONFIG_NAME
    if not fp.exists():
        fp.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
    return fp


def _resolve(root: Path, paths) -> list[Path]:
    out = []
    for p in paths or []:
        q = Path(p)
        out.append(q if q.is_absolute() else root / q)
    return out


def _enrich_ocr(rows: list[dict], root: Path, verbose: bool = True) -> dict:
    """补 OCR 文字识别（带缓存）。OCR 是文字判定的可靠依据，未接时自动降级为软警告。"""
    from . import ocr
    ok, desc = ocr.available()
    if not ok:
        print(f"  文字识别(OCR)不可用：{desc}")
        print("  -> 文字判定降级为软警告（不会因纹理误判扣重分）。")
        print("     想启用：python -m pip install winsdk")
        return {"available": False, "desc": desc}
    todo = [r for r in rows if r.get("ok") and "ocr_ok" not in r]
    if not todo:
        stat = {"total": len(rows), "cached": len(rows), "ocr_ok": sum(
            1 for r in rows if r.get("ocr_ok")), "with_text": sum(
            1 for r in rows if r.get("ocr_chars", 0) >= 4)}
    else:
        stat = ocr.enrich(rows, root / "work" / "cache" / "ocr.json", verbose=verbose)
    stat["available"] = True
    stat["desc"] = desc
    print(f"  OCR({desc.split('，')[0]})：识别成功 {stat.get('ocr_ok', 0)}/{len(rows)} 张，"
          f"其中 {stat.get('with_text', 0)} 张含文字，缓存命中 {stat.get('cached', 0)}")
    return stat


def _extract_all(paths: list[Path], root: Path, workers_note: bool = True) -> tuple[list[dict], dict]:
    files = io_utils.find_images(paths)
    if not files:
        return [], {"files": 0, "hits": 0, "misses": 0, "seconds": 0.0}
    cache = io_utils.FeatureCache(root / "work" / "cache" / "features.json")
    rows: list[dict] = []
    t0 = time.time()
    for i, fp in enumerate(files, 1):
        f = cache.get(fp)
        if f is not None:
            f = dict(f)
        else:
            f = features.extract(fp)
            if f:
                cache.put(fp, f)
        if f:
            rows.append(f)
        if workers_note and i % 50 == 0:
            print(f"    处理 {i}/{len(files)}…")
    cache.save()
    return rows, {"files": len(files), "hits": cache.hits, "misses": cache.misses,
                  "seconds": round(time.time() - t0, 1)}


# --------------------------------------------------------------------------
def cmd_doctor(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    print("=" * 74)
    print("环境体检")
    print("=" * 74)
    print(f"  工作目录      : {root}")
    print(f"  Python        : {sys.version.split()[0]}")
    print(f"  numpy / Pillow: {np.__version__} / ", end="")
    try:
        import PIL
        print(PIL.__version__)
    except Exception:
        print("缺失")
    try:
        import requests
        print(f"  requests      : {requests.__version__}")
    except Exception:
        print("  requests      : 缺失（抓图需要：python -m pip install requests）")
    try:
        import sklearn
        print(f"  scikit-learn  : {sklearn.__version__}（可选）")
    except Exception:
        print("  scikit-learn  : 未安装（可选，不影响）")

    print()
    print("=" * 74)
    print("样本检查")
    print("=" * 74)
    sets = {}
    for key, label in (("approved_dirs", "过审"), ("rejected_dirs", "被刷"),
                       ("reference_dirs", "参考")):
        dirs = _resolve(root, cfg.get(key) or [])
        files = io_utils.find_images(dirs)
        sets[label] = files
        mark = "OK " if files else "-- "
        exists = ", ".join(str(d) for d in dirs) or "(未配置)"
        print(f"  {mark}{label}: {len(files):5d} 张   <- {exists}")
        if not files and key != "reference_dirs":
            print(f"      提示: 建目录并把图放进去 → {dirs[0] if dirs else ''}")

    na, nr = len(sets.get("过审", [])), len(sets.get("被刷", []))

    # 说明当前实际生效的画像/模型来自哪里 —— 它们可能不是上面这个 samples/ 目录
    # （实测：画像是用 作者本地的商品图目录 里 50 张成功 mockup + 126 张被刷 mockup 训练的，
    #  而 samples/ 里只有 8+36 张，不说明会让人以为画像没生效）
    prof_fp = root / "work" / "profile.json"
    if prof_fp.exists():
        try:
            pj = json.loads(prof_fp.read_text(encoding="utf-8"))
            src = pj.get("source") or "（未记录来源）"
            print(f"\n  当前生效的画像: 过审 {pj.get('n_approved', 0)} 张 / "
                  f"被刷 {pj.get('n_rejected', 0)} 张")
            print(f"    数据来源: {src}")
            print(f"    注意: 这与上面 samples/ 里的张数可能不同 —— "
                  f"samples/ 只是你手动放样本的位置，画像是用实际标注数据训练的。")
        except Exception:
            pass

    print()
    if na == 0:
        print("  !! 一张过审图都没有：现在只能做硬规则合规检查，无法学风格。")
        print("     建议先收集 20~50 张同类目里卖得好/已上架的图放进 samples/过审。")
    elif na < 5:
        print(f"  !! 过审样本只有 {na} 张，画像不可靠（建议 ≥5，最好 ≥20）。")
    else:
        print(f"  OK 过审样本 {na} 张，可以建画像。", end="")
        if nr < 4:
            print(f" 但被刷样本只有 {nr} 张（建议 ≥4），判别模型会降权甚至不训练。")
        else:
            print(f" 被刷样本 {nr} 张，可以训练判别模型。")

    print()
    print("=" * 74)
    print("图源可达性（不抓 SHEIN 前台 —— 见 README 合规说明）")
    print("=" * 74)
    try:
        from . import crawl
        for name, fn, kw in (("Bing 图片", crawl.search_bing, {"pages": 1}),
                             ("Pexels", crawl.search_pexels, {}),
                             ("Pixabay", crawl.search_pixabay, {})):
            try:
                got = fn("throw blanket", **kw) if name == "Bing 图片" else fn("blanket")
                print(f"  OK  {name:12s} 解析到 {len(got)} 条图片链接")
            except Exception as e:
                print(f"  --  {name:12s} 不可用: {type(e).__name__}: {str(e)[:60]}")
    except ImportError:
        print("  跳过（缺 requests）")

    print()
    print("=" * 74)
    print("下一步建议")
    print("=" * 74)
    if na >= 5:
        print("  1) python -m shein_scout build")
        print("  2) python -m shein_scout score samples/过审 samples/被刷  # 先验证判定是否靠谱")
    else:
        print("  1) 收集过审图 -> samples/过审 ；被刷图 -> samples/被刷")
    print("  3) python -m shein_scout crawl \"午休毯 白底 产品图\" -n 60")
    print(f"\n  配置文件: {ensure_config(root)}")
    return 0


def cmd_crawl(args) -> int:
    root = Path(args.root).resolve()
    from . import crawl
    out_dir = Path(args.out) if args.out else root / "pool" / "网络候选"
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    print("=" * 74)
    print(f"搜索: {args.query}")
    print("=" * 74)
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    cands = crawl.search(args.query, sources=sources, pages=args.pages)
    print(f"  合计候选 {len(cands)} 条")
    if not cands:
        print("  没有拿到候选链接，换个关键词或图源再试。")
        return 1
    if args.dry_run:
        for c in cands[:20]:
            print(f"    {c['source']:8s} {c['url'][:110]}")
        print("  (--dry-run，未下载)")
        return 0
    print(f"  开始下载到 {out_dir} …")
    st = crawl.download(cands, out_dir, limit=args.limit, min_side=args.min_side,
                        allow_stock=args.allow_stock)
    print()
    print(f"  保存 {st['saved']} 张 | 跳过付费图库 {st['skipped_stock']} | "
          f"重复 {st['dup_exact'] + st['dup_near']} | 过小 {st['too_small']} | 失败 {st['failed']}")
    print(f"  下一步: python -m shein_scout score \"{out_dir}\"")
    return 0


def cmd_build(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    print("=" * 74)
    print("学习口径：从你的过审/被刷样本里统计风格画像")
    print("=" * 74)

    mockup_only = not getattr(args, "all_files", False)

    def load_set(key: str, label: str) -> list[dict]:
        dirs = _resolve(root, cfg.get(key) or [])
        files = io_utils.find_images(dirs)
        before = len(files)
        if mockup_only:
            # 只保留成品展示图。踩过的坑：把尺寸图/信息图混进画像会让统计失真 ——
            # 同一个文件夹里 mockup 与杂项的饱和度能差 0.10 以上。
            files = [f for f in files if "mockup" in f.name.lower()]
            if before != len(files):
                print(f"  {label}: 已过滤杂项，{before} -> {len(files)} 张"
                      f"（只保留文件名含 mockup 的成品展示图）")
        rows, st = _extract_all(files, root)
        print(f"  {label}: {len(rows)} 张有效（扫描 {st['files']} 个文件，"
              f"缓存命中 {st['hits']}，用时 {st['seconds']}s）")
        return rows

    approved = load_set("approved_dirs", "过审")
    rejected = load_set("rejected_dirs", "被刷")
    reference = load_set("reference_dirs", "参考")

    if not approved:
        print("\n!! 没有过审样本，无法建画像。")
        print("   请把已上架/已过审的同类目图片放进 samples/过审 后重跑。")
        return 1

    # 保护：如果新画像的样本数远少于现有画像，说明配置指错了目录，
    # 直接覆盖会让判定质量大幅下降（踩过：samples/ 只有 8 张，
    # 而真实画像是用 C:\\图片 里 50 张成功 mockup 训练的）。
    old_fp = root / "work" / "profile.json"
    if old_fp.exists():
        try:
            oldp = json.loads(old_fp.read_text(encoding="utf-8"))
            n_old = int(oldp.get("n_approved", 0) or 0)
            if n_old > len(approved) * 2 and n_old >= 10 and not getattr(args, "force", False):
                print()
                print(f"  !! 中止：现有画像用了 {n_old} 张过审样本，"
                      f"而本次只有 {len(approved)} 张。")
                print(f"     这通常是 config.json 的 approved_dirs 指错了目录。")
                print(f"     现有画像: {old_fp}")
                print(f"     想强行覆盖请加 --force。")
                return 1
        except Exception:
            pass

    prof = learn.build_profile(approved, rejected, reference)
    model_info = None
    logit = None
    if prof["status"] == "ok" and len(rejected) >= 4:
        mcfg = cfg.get("model") or {}
        logit = learn.Logit(l2=float(mcfg.get("l2", 1.2)),
                            iters=int(mcfg.get("iters", 2500)),
                            lr=float(mcfg.get("lr", 0.6)))
        logit.fit(approved, rejected, learn.MODEL_FEATURES)
        rel, desc = learn.model_reliability(logit, len(approved), len(rejected))
        model_info = {"reliability": rel, "desc": desc, "coefs": logit.coef_table(14),
                      "train_acc": logit.train_acc, "loo_acc": logit.loo_acc,
                      "loo_auc": logit.loo_auc}
        print()
        print("  判别模型（过审 vs 被刷）")
        print(f"    训练样本     : {len(approved)} 过审 / {len(rejected)} 被刷")
        print(f"    训练集准确率 : {logit.train_acc:.3f}（有乐观偏差，别当真实水平）")
        if logit.loo_auc is not None:
            print(f"    留一交叉验证 : AUC={logit.loo_auc:.3f}  准确率={logit.loo_acc:.3f}  <- 看这个")
        else:
            print("    留一交叉验证 : 样本不足，未做（模型权重会自动降权）")
        print(f"    可信度       : {rel:.2f}  ({desc})")
        print("    最能区分的特征:")
        for c in model_info["coefs"][:8]:
            print(f"      {c['name']:14s} 系数 {c['coef']:+.3f}  -> {c['direction']}")

    # 画像可读卡片
    prof_path = root / "work" / "profile.json"
    io_utils.write_json(prof_path, prof)
    if logit is not None:
        io_utils.write_json(root / "work" / "model.json",
                            {"logit": logit.to_json(), "info": model_info,
                             "n_approved": len(approved), "n_rejected": len(rejected)})

    if prof["status"] == "ok":
        print()
        print("=" * 74)
        print("你的过审图长什么样（画像摘要）")
        print("=" * 74)
        st = prof["stats"]
        show = [("white_ratio", "纯白底占比"), ("corner_bright", "四角亮度"),
                ("subject_area", "主体面积占比"), ("subject_centered", "主体居中"),
                ("bright_mean", "整体亮度"), ("sat_mean", "平均饱和度"),
                ("colorfulness", "色彩鲜艳度"), ("edge_density", "细节密度"),
                ("lap_var_256", "清晰度"), ("warmth_ab", "色温(黄蓝)")]
        for key, label in show:
            s = st.get(key)
            if s:
                print(f"  {label:14s} 典型 {s['median']:.3f}   常见区间 {s['p10']:.3f} ~ {s['p90']:.3f}")
        print(f"\n  背景风格     : "
              f"{ {'white': '以白底为主', 'scene': '以场景图为主', 'mixed': '白底/场景混用'}[prof['dominant_bg']] }"
              f"（白底图占 {prof['white_bg_share'] * 100:.0f}%）")
        print(f"  风格一致性   : {prof['style_cohesion']:.2f}（越高说明你这批图越统一）")
        print(f"  过审图实际尺寸样本: " + ", ".join(
            f"{d['w']}x{d['h']}" for d in prof["real_size_sample"][:6]))
    else:
        print(f"\n  !! {prof.get('note')}")

    print()
    print(f"  画像已存: {prof_path}")
    print("  下一步  : python -m shein_scout score samples/过审 samples/被刷")
    return 0


def _load_profile_model(root: Path):
    prof = io_utils.read_json(root / "work" / "profile.json", default=None)
    if prof is None:
        prof = {"stats": {}, "n_approved": 0, "n_rejected": 0,
                "status": "insufficient", "note": "还没跑 build"}
    logit = None
    minfo = None
    mm = io_utils.read_json(root / "work" / "model.json", default=None)
    if mm and mm.get("logit"):
        try:
            logit = learn.Logit.from_json(mm["logit"])
            minfo = mm.get("info")
        except Exception:
            logit = None
    return prof, logit, minfo


def _is_excluded(fp: Path, root: Path) -> bool:
    """用户直接跑 `score`（不带参数＝扫整个目录）时，排除不该混进来的东西：
    样本目录（它们是训练数据，不是候选图）、缓存/产物目录、开发自检图。"""
    try:
        rel = fp.resolve().relative_to(root.resolve())
    except Exception:
        return False
    parts = [p.lower() for p in rel.parts]
    if not parts:
        return False
    skip_dirs = {"work", "runs", "__pycache__", "dev", ".git"}
    if any(p in skip_dirs for p in parts[:-1]):
        return True
    sample_names = {str(d).lower() for d in
                    ("samples",)}
    if parts[0] in sample_names:
        return True
    return False


def cmd_score(args) -> int:
    root = Path(args.root).resolve()
    cfg = load_config(root)
    rules = dict(scoring.DEFAULT_RULES)
    rules.update(cfg.get("rules") or {})
    # 区分"用户显式指定了路径"和"没给路径于是默认扫全目录"。
    # 不能用 bool(args.paths) 判断 —— main() 会把空值兜底成 ["."]，那样永远为真。
    user_gave_paths = [p for p in (args.paths or []) if p not in (".", "./", "")]
    targets = [Path(p) if Path(p).is_absolute() else root / p
               for p in (user_gave_paths or ["."])]
    print("=" * 74)
    print("打分排序")
    print("=" * 74)
    rows, st = _extract_all(targets, root)
    if not user_gave_paths:
        before = len(rows)
        rows = [r for r in rows if not _is_excluded(Path(r.get("file", "")), root)]
        if before != len(rows):
            print(f"  已排除 {before - len(rows)} 张（样本目录/缓存/开发自检图，不是候选图）")
    if not rows:
        print("!! 没找到可读的图片。检查路径是否正确。")
        return 1
    print(f"  读取 {len(rows)} 张（扫描 {st['files']} 个文件，缓存命中 {st['hits']}，用时 {st['seconds']}s）")
    if not args.no_ocr:
        _enrich_ocr(rows, root)
    else:
        print("  已跳过 OCR（--no-ocr）：文字判定为软警告")

    prof, logit, minfo = _load_profile_model(root)
    if not prof.get("stats"):
        print("  提示: 还没有风格画像（work/profile.json 不存在），本次只做硬规则检查。")
        print("        想让判定贴合你的类目，请先放样本再跑 build。")
    # 同上：报告头部显示实际参与打分的模型
    try:
        _cm_p = root / "work" / "color_model.json"
        if _cm_p.exists():
            _cm = json.loads(_cm_p.read_text(encoding="utf-8"))
            _m = _cm.get("metrics") or {}
            minfo = {
                "desc": (f"打分模型：色彩口径（9 特征），"
                         f"留一文件夹准确率 {_m.get('leave_one_folder_accuracy', 0):.1%}"
                         f"（95% CI {_m.get('leave_one_folder_ci95', [0, 0])[0]:.2f}"
                         f"~{_m.get('leave_one_folder_ci95', [0, 1])[1]:.2f}）"
                         if _m else "打分模型：色彩口径（9 特征）")
            }
    except Exception:
        pass
    # 同上：报告头部显示实际参与打分的模型
    try:
        _cm_p = root / "work" / "color_model.json"
        if _cm_p.exists():
            _cm = json.loads(_cm_p.read_text(encoding="utf-8"))
            _m = _cm.get("metrics") or {}
            minfo = {
                "desc": (f"打分模型：色彩口径（9 特征），"
                         f"留一文件夹准确率 {_m.get('leave_one_folder_accuracy', 0):.1%}"
                         f"（95% CI {_m.get('leave_one_folder_ci95', [0, 0])[0]:.2f}"
                         f"~{_m.get('leave_one_folder_ci95', [0, 1])[1]:.2f}）"
                         if _m else "打分模型：色彩口径（9 特征）")
            }
    except Exception:
        pass
    # 同上：报告头部显示实际参与打分的模型
    try:
        _cm_p = root / "work" / "color_model.json"
        if _cm_p.exists():
            _cm = json.loads(_cm_p.read_text(encoding="utf-8"))
            _m = _cm.get("metrics") or {}
            minfo = {
                "desc": (f"打分模型：色彩口径（9 特征），"
                         f"留一文件夹准确率 {_m.get('leave_one_folder_accuracy', 0):.1%}"
                         f"（95% CI {_m.get('leave_one_folder_ci95', [0, 0])[0]:.2f}"
                         f"~{_m.get('leave_one_folder_ci95', [0, 1])[1]:.2f}）"
                         if _m else "打分模型：色彩口径（9 特征）")
            }
    except Exception:
        pass
    # 收集训练样本路径，用于标记数据泄漏
    train_files: set[str] = set()
    for key in ("approved_dirs", "rejected_dirs"):
        for d in _resolve(root, cfg.get(key) or []):
            for fp in io_utils.find_images([d]):
                train_files.add(str(fp).lower())
    ft = getattr(args, "force_type", "candidate")
    _mode_zh = {"candidate": "产品展示图（标注疑似设计稿）", "product": "产品展示图",
                "design": "印花图案稿", "auto": "自动分型"}
    print(f"  评估口径: {_mode_zh.get(ft, ft)}")
    scored = scoring.score_rows(rows, prof, logit, rules, train_files=train_files,
                                force_type=ft)
    n_leak = sum(1 for r in scored if r.get("used_in_training"))
    if n_leak:
        print(f"  注意: {n_leak} 张图是训练样本，已在报告中标记（分数偏乐观，不能当验证依据）")

    out_dir = root / "runs" / time.strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    # 注意：凡是字符串/非数值字段都必须显式列出 —— features.feature_columns() 只收数值列。
    # （踩过两次：reasons 和 ocr_text 都因此整列消失）
    lead = ["name", "img_type_zh", "preference", "verdict", "style_match",
            "model_score", "model_used", "ood_flag", "rule_score",
            "width", "height", "file",
            "reasons", "note", "ocr_chars", "ocr_text", "ocr_error"]
    csv_cols = lead + [c for c in features.feature_columns(scored) if c not in lead]
    flat = []
    for r in scored:
        rr = dict(r)
        rr["reasons"] = " | ".join(str(i.get("msg", "")) for i in (r.get("reasons") or []))
        rr["reasons"] = rr["reasons"][:900]
        rr.pop("highlights", None)
        rr.pop("weights_used", None)
        flat.append(rr)
    csv_path = out_dir / "scores.csv"
    io_utils.write_csv(csv_path, flat, csv_cols)
    html_path = report.build_report(scored, out_dir / "report.html", prof, minfo)

    _n_adj = sum(1 for r in scored if (r.get("adjust") or {}).get("needed"))
    if _n_adj:
        print()
        print(f"  {_n_adj} 张的色彩偏艳（按你自己成功款的分布判断）。")
        print("  口径说明：这条判据刻画的是【你店铺已被接受的风格】，不是平台级硬门槛 ——")
        print("            他人成功上架图的饱和度横跨 0.10~0.56，平台并不要求低饱和。")

    _ood = next((r.get("ood") for r in scored if r.get("ood")), None)
    if _ood and _ood.get("ood"):
        print()
        sw = _ood.get("switched_to")
        tag = {"color_model_default": "色彩口径模型",
               "full_model_fallback": "完整模型(回退)",
               "color_model": "色彩口径模型",
               "downweighted": "模型已降权"}.get(sw, "未切换")
        print(f"  ⚠ 分布外提示（{tag}）：" + str(_ood.get("reason")))

    cnt: dict[str, int] = {}
    tcnt: dict[str, int] = {}
    for r in scored:
        cnt[r.get("verdict", "?")] = cnt.get(r.get("verdict", "?"), 0) + 1
        t = r.get("img_type_zh") or "未识别"
        tcnt[t] = tcnt.get(t, 0) + 1
    print()
    print("  图型分布: " + " | ".join(f"{k} {v}" for k, v in sorted(tcnt.items())))
    print("  判定分布: " + " | ".join(f"{k} {v}" for k, v in sorted(cnt.items())))
    print("  最该注意的 5 张:")
    for r in sorted(scored, key=lambda x: float(x.get("preference") or 0))[:5]:
        why = (r.get("reasons") or [{}])[0].get("msg", "")
        print(f"    {float(r.get('preference') or 0):5.1f}  {r.get('name', '')[:34]:34s} {why[:52]}")
    print()
    print(f"  报告: {html_path}")
    print(f"  表格: {csv_path}")
    return 0


def cmd_fix(args) -> int:
    """一条命令完成：打分 + 输出降饱和成品图 + 清单。"""
    from . import adjust
    root = Path(args.root).resolve()
    cfg = load_config(root)
    rules = dict(scoring.DEFAULT_RULES)
    rules.update(cfg.get("rules") or {})
    targets = [Path(p) if Path(p).is_absolute() else root / p for p in args.paths]
    out_dir = Path(args.out) if args.out else None
    if out_dir is None:
        out_dir = root / "runs" / time.strftime("%Y%m%d-%H%M%S") / "fixed"
    elif not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 74)
    print("批量处理：打分 + 调色成品输出")
    print("=" * 74)

    # 只处理成品展示图。实战踩坑：对商品文件夹直接跑，尺寸图/信息图/印花稿
    # 也被处理并"降饱和"了 —— 那些图根本不该调色，纯属浪费与误导。
    all_files = io_utils.find_images(targets)
    if not getattr(args, "all_files", False):
        mockups = [f for f in all_files if "mockup" in f.name.lower()]
        if mockups:
            skipped = len(all_files) - len(mockups)
            if skipped:
                print(f"  跳过 {skipped} 个非成品图（尺寸图/信息图/印花稿），"
                      f"只处理 {len(mockups)} 张 mockup")
                print(f"    （想全部处理加 --all-files）")
            targets = mockups
        else:
            print("  提示: 文件名里没有 'mockup' 的图，本次按全部文件处理。")
            print("        如果你的成品图是别的命名，可以加 --all-files 强制全部处理。")

    rows, st = _extract_all(targets, root)
    if not rows:
        print("!! 没找到可读的图片")
        return 1
    print(f"  读取 {len(rows)} 张（缓存命中 {st['hits']}，用时 {st['seconds']}s）")
    if not args.no_ocr:
        _enrich_ocr(rows, root, verbose=False)

    prof, logit, minfo = _load_profile_model(root)
    if not prof.get("stats"):
        print("  !! 没有风格画像，无法给出调色建议。请先跑 build。")
        return 1
    # 报告头部要显示**实际参与打分的模型**。
    # 打分链路内部会换成色彩口径模型，若这里仍报完整模型的 AUC 会误导用户。
    try:
        _cm_p = root / "work" / "color_model.json"
        if _cm_p.exists():
            _cm = json.loads(_cm_p.read_text(encoding="utf-8"))
            _m = _cm.get("metrics") or {}
            minfo = {
                "desc": (f"打分模型：色彩口径（9 特征），"
                         f"留一文件夹准确率 {_m.get('leave_one_folder_accuracy', 0):.1%}"
                         f"（95% CI {_m.get('leave_one_folder_ci95', [0, 0])[0]:.2f}"
                         f"~{_m.get('leave_one_folder_ci95', [0, 1])[1]:.2f}）"
                         if _m else "打分模型：色彩口径（9 特征）")
            }
    except Exception:
        pass
    train_files: set[str] = set()
    for key in ("approved_dirs", "rejected_dirs"):
        for d in _resolve(root, cfg.get(key) or []):
            for fp in io_utils.find_images([d]):
                train_files.add(str(fp).lower())

    ft = getattr(args, "force_type", "candidate")
    scored = scoring.score_rows(rows, prof, logit, rules,
                               train_files=train_files, force_type=ft)
    plan_rows = []
    n_fixed = 0
    for r in scored:
        adj = r.get("adjust") or {}
        if not adj.get("needed") or not adj.get("factors"):
            plan_rows.append({"name": r.get("name"), "action": "无需处理",
                              "preference": r.get("preference"),
                              "verdict": r.get("verdict"), "detail": ""})
            continue
        strong = adj["factors"][0]
        proj = adj.get("projection") or {}
        lead, lead_ratio = None, 0.0
        for k, d in proj.items():
            t = d.get("threshold") or 0
            if t > 0 and d["now"] > t:
                ratio = d["now"] / t
                if ratio > lead_ratio:
                    lead, lead_ratio = k, ratio
        NAME = {"sat_mean": "饱和度", "colorfulness": "鲜艳度", "sat_p90": "高饱和占比"}
        src = Path(r.get("file", ""))
        dst = out_dir / (f"{src.stem}_降饱和{int(round((1 - strong) * 100))}pct{src.suffix}")
        res = adjust.apply_to_file(src, dst, strong)
        if res:
            n_fixed += 1
        d = proj.get(lead, {}) if lead else {}
        plan_rows.append({
            "name": r.get("name"), "action": f"已降饱和 {int(round((1 - strong) * 100))}%",
            "preference": r.get("preference"), "verdict": r.get("verdict"),
            "detail": f"{NAME.get(lead, lead or '-')} {d.get('now')} -> 预估 {d.get('after')}"
                      f"（阈值 {d.get('threshold')}）",
            "output": res.name if res else ""})

    n_need = sum(1 for x in plan_rows if x["action"].startswith("已降"))
    print()
    if n_need:
        print("  口径说明：本条色彩判据是从你 50 张成功 / 210 张被刷的图里学出来的，")
        print("            它刻画的是【你自己店铺已被接受的风格】，不是平台级硬门槛。")
        print("            （已用他人成功上架图验证：他们的饱和度横跨 0.10~0.56，")
        print("              说明平台并不要求低饱和。请把它当作\"贴近你自己成功款\"的参考。）")
    print(f"  需要调色: {n_need} 张 / 共 {len(scored)} 张")
    print(f"  已输出成品: {n_fixed} 张 -> {out_dir}")
    csv_path = out_dir.parent / "处理清单.csv"
    io_utils.write_csv(csv_path, plan_rows)
    html_path = report.build_report(scored, out_dir.parent / "report.html", prof, minfo,
                                    title="批量处理报告")
    print()
    print(f"  处理清单: {csv_path}")
    print(f"  看图报告: {html_path}")
    if n_fixed:
        print()
        print("  输出目录里的图可直接上传（文件名已带降幅，不会认错）。")
    return 0


def cmd_productcrawl(args) -> int:
    """竞品选品爬虫：按关键词搜商品，产出主图链接 + 商品信息 + 仪表盘。"""
    import time as _t
    from . import productcrawl as pc
    from . import productreport as pr

    root = Path(args.root).resolve()
    kws = [k.strip() for k in (args.keywords or []) if k.strip()]
    if not kws:
        print("  请至少给一个关键词。例：")
        print('    python -m shein_scout productcrawl "fall throw blanket" -n 2')
        return 1

    srcs = tuple(x.strip() for x in (args.sources or "amazon").split(",") if x.strip())
    print("=" * 78)
    print("竞品选品爬虫")
    print("=" * 78)
    print(f"  关键词: {', '.join(kws)}")
    print(f"  来源  : {', '.join(srcs)}")
    print(f"  页数  : {args.pages}（每页约 48 个商品）")
    print(f"  排序  : {args.sort or '搜索相关度'}")
    print()

    items = pc.crawl(kws, sources=srcs, pages=args.pages, sort=args.sort)
    n_raw = len(items)
    items = pc.dedup(items)
    print(f"\n  抓到 {n_raw} 条，去重后 {len(items)} 条")

    if args.top and len(items) > args.top:
        items = pc.sort_by_popularity(items)[:args.top]
        print(f"  按销量信号取前 {len(items)} 条")

    if not items:
        print("  没有抓到商品。可能被限流，稍后再试或减少页数。")
        return 1

    # 只保留有主图的（用户要的是主图链接）
    if not args.keep_no_image:
        before = len(items)
        items = [it for it in items if it.main_image]
        if len(items) < before:
            print(f"  过滤掉 {before - len(items)} 个没有主图的商品")

    # 重新编号
    for i, it in enumerate(items, 1):
        it.rank = i

    stamp = _t.strftime("%Y%m%d-%H%M%S")
    out_dir = root / "runs" / f"选品-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    cur = pc.currency_of(items)
    csv_p = pr.write_csv(items, out_dir / "products.csv")
    dash_p = pr.write_dashboard(
        items, out_dir / "dashboard.html",
        title=f"竞品选品 · {', '.join(kws)}", currency=cur)

    n_img = sum(1 for it in items if it.main_image)
    n_sig = sum(1 for it in items if it.bought)
    print()
    print(f"  商品数    : {len(items)}（{n_img} 个有主图，{n_sig} 个有销量信号）")
    if cur:
        print(f"  价格币种  : {cur}（Amazon 按网络位置返回，非美元时需自行换算）")
    print(f"  商品表    : {csv_p}")
    print(f"  仪表盘    : {dash_p}")
    print()
    print("  打开仪表盘可以：看图、按销量排序、点链接跳商品页、点「下载主图」存原图。")
    print("  本地没有批量下载图片（按你的要求，只给链接）。")
    return 0


def cmd_vlm(args) -> int:
    """视觉模型（火山方舟/豆包）接入工具。"""
    from . import vlm
    root = Path(args.root).resolve()

    ok, desc = vlm.available()
    print("=" * 74)
    print("视觉模型（火山方舟 / 豆包）")
    print("=" * 74)
    print(f"  环境检查: {'就绪' if ok else '未就绪'} —— {desc}")
    print(f"  接口地址: {vlm.ENDPOINT}")
    print(f"  官方文档: {vlm.DOC_URL}")
    if not ok:
        print()
        print("  设置方式（PowerShell，当前会话有效）：")
        print('    $env:ARK_API_KEY = "你的密钥"')
        print("  永久生效：把上面这行加到 PowerShell 的 $PROFILE 里。")
        print("  Key 从这里申请：火山方舟控制台 → API Key 管理")
        print()
        print("  可选：指定模型（默认用候选列表第一个）")
        print('    $env:ARK_MODEL = "doubao-seed-2-1-pro-260628"')
        return 1

    if args.probe:
        img = args.image
        if not img:
            print("  请用 --image 指定一张真实图片用于探测")
            return 1
        print()
        print(f"  逐个体检候选模型（测试图：{img}）")
        vlm.probe_models(test_image=img)
        print()
        print("  把可用的那个设为默认：")
        print('    $env:ARK_MODEL = "<可用的模型名>"')
        return 0

    if args.image:
        print()
        print(f"  单张测试: {args.image}")
        r = vlm.extract(args.image, model=args.model)
        if r["ok"]:
            print("  返回字段:")
            for k, v in r["fields"].items():
                print(f"    {k}: {v}")
        else:
            print(f"  失败: {r['error']}")
            return 1
        return 0

    if args.batch:
        files = io_utils.find_images([args.batch])
        if not files:
            print(f"  没找到图片: {args.batch}")
            return 1
        print()
        print(f"  批量提取 {len(files)} 张（串行 + 缓存，重复跑不重复计费）")
        rows = vlm.extract_many(files, model=args.model)
        n_ok = sum(1 for r in rows if r["ok"])
        out_dir = root / "runs" / time.strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
        flat = []
        for r in rows:
            d = {"name": r["name"], "ok": r["ok"], "error": r["error"]}
            d.update(r.get("fields") or {})
            flat.append(d)
        csv_p = out_dir / "vlm_标签.csv"
        io_utils.write_csv(csv_p, flat)
        print()
        print(f"  成功 {n_ok}/{len(rows)}")
        print(f"  标签表: {csv_p}")
        return 0

    print()
    print("  用法:")
    print("    python -m shein_scout vlm --probe --image <一张图>   # 探测哪个模型可用")
    print("    python -m shein_scout vlm --image <一张图>            # 单张测试")
    print("    python -m shein_scout vlm --batch <目录>             # 批量打标签")
    return 0







def cmd_season(args) -> int:
    """档期 × 选品联动：同时看多市场档期，给出标签推荐和抓图关键词。"""
    from . import season

    root = Path(args.root).resolve()
    mks = [m.strip().upper() for m in (args.markets or "").split(",") if m.strip()]
    lead = {"海运": args.sea, "空运": args.air}
    rows = season.scan(markets=mks or None, year=args.year, lead=lead,
                       horizon=args.days)

    title = ", ".join(mks) if mks else "全部市场"
    print("=" * 100)
    print(f"档期 × 选品联动    {title}    未来 {args.days} 天    "
          f"（今天 {__import__('datetime').date.today()}）")
    print("=" * 100)

    if not rows:
        print("  未来这段时间没有需要准备的商业节点。")
        return 0

    hdr = (f"{'市场':5s} {'节日':10s} {'节日日期':11s} "
           f"{'海运截止':11s} {'空运截止':11s} {'状态':14s} 推荐主题")
    print(hdr)
    print("-" * 100)
    for r in rows:
        sea = r["截止"].get("海运")
        air = r["截止"].get("空运")
        print(f"{r['市场名']:5s} {r['节日']:10s} {str(r['日期']):11s} "
              f"{str(sea or '-'):11s} {str(air or '-'):11s} {r['状态']:14s} "
              f"{'/'.join(r['主题'])}")

    act = [r for r in rows if r["级别"] <= 1]
    if act:
        print()
        print("=" * 100)
        print(f"需要动手的 {len(act)} 个")
        print("=" * 100)
        for r in act:
            sea, air = r["剩余"].get("海运"), r["剩余"].get("空运")
            print(f"\n【{r['市场名']} · {r['节日']}】节日 {r['日期']}   {r['状态']}")
            print(f"  海运({args.sea}天): 最晚 {r['截止'].get('海运')} "
                  f"（{'剩 ' + str(sea) + ' 天' if sea is not None and sea >= 0 else '来不及'}）")
            print(f"  空运({args.air}天): 最晚 {r['截止'].get('空运')} "
                  f"（{'剩 ' + str(air) + ' 天' if air is not None and air >= 0 else '来不及'}）")
            print(f"  推荐主题: {', '.join(r['主题']) or '—'}")
            print(f"  推荐配色: {', '.join(r['配色']) or '—'}")
            print(f"  推荐风格: {', '.join(r['风格']) or '—'}")
            print(f"  画面元素: {', '.join(r['元素']) or '—'}")
            if r["备注"]:
                print(f"  注意    : {r['备注']}")
            print("  抓图关键词:")
            for k in r["关键词"][:6]:
                print(f"      {k}")
            if args.crawl_cmd:
                kws = " ".join(f'"{k}"' for k in r["关键词"][:3])
                print(f"  直接抓图: python -m shein_scout patterncrawl {kws} --want 120")

    past = [r for r in rows if r["级别"] == 3]
    if past:
        print()
        print("=" * 100)
        print(f"已错过（{len(past)} 个，明年再说）")
        print("=" * 100)
        for r in past[:8]:
            print(f"  {r['市场名']} {r['节日']:10s} {r['日期']}  "
                  f"（海运需 {r['截止'].get('海运')} 前下单）")

    if args.save:
        import json
        out = root / "runs" / "档期" / "season.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            [{**r, "日期": str(r["日期"]),
              "截止": {k: str(v) for k, v in r["截止"].items()}}
             for r in rows], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  已存: {out}")
    return 0


def cmd_patterncrawl(args) -> int:
    """爬一批图案图 -> 入库 -> （有模型则排序）-> 生成打标签页。"""
    from . import labeler, library, patterncrawl as pc, preflearn

    root = Path(args.root).resolve()
    kws = [k.strip() for k in (args.keywords or []) if k.strip()]
    if not kws:
        print("  请给关键词。例：")
        print('    python -m shein_scout patterncrawl "maple leaf pattern" "ghost seamless"')
        return 1

    srcs = tuple(x.strip() for x in (args.sources or "360,bing").split(",") if x.strip())
    per = max(20, args.want // max(1, len(kws)))
    st = library.stats(root)

    print("=" * 78)
    print("爬图案图")
    print("=" * 78)
    print(f"  关键词: {', '.join(kws)}")
    print(f"  来源  : {', '.join(srcs)}    目标: 约 {args.want} 张")
    print(f"  图案库现状: 共 {st['total']} 张（好 {st['good']} / 坏 {st['bad']} / "
          f"未标 {st['unlabeled']}）")
    model = preflearn.load_model(root)
    if model:
        print("  已学到的偏好模型: 有 —— 本批会按你的偏好排序")
    else:
        print("  已学到的偏好模型: 无（先标一批，跑 patternlearn 就能用上）")
    print()

    out_root = root / "patterns"
    bname = (getattr(args, "name", "") or "").strip()
    batch, imgs = pc.crawl_batch(kws, out_root, want_per_kw=per, sources=srcs,
                                 min_side=args.min_side, batch=bname,
                                 junk_filter=not getattr(args, "no_junkfilter",
                                                        False))
    if not imgs:
        print("\n  没爬到图片。换个关键词或稍后再试。")
        return 1
    pc.save_manifest(imgs, out_root, batch)

    # 归入存档目录（用硬链接，不占额外空间）
    arch = (getattr(args, "archive", "") or "").strip()
    if arch:
        try:
            r = pc.link_to_archive(imgs, out_root, batch, Path(arch))
            print(f"\n  已归档到 {r['dir']}")
            print(f"      硬链接 {r['linked']} 张，复制 {r['copied']} 张，"
                  f"失败 {r['failed']} 张")
        except Exception as e:
            print(f"\n  归档失败: {type(e).__name__}: {e}")

    # 入库
    n_new = library.add_from_manifest(root, batch)
    recs = [r for r in library.load(root) if r.batch == batch]

    # ---- 自动给新图打标签（排序的依据） ----
    ordered = recs
    if args.tag and os.environ.get("ARK_API_KEY"):
        from . import tagger
        print(f"\n  AI 打标签（{len(recs)} 张）...")
        tres = tagger.tag_many(recs, root, verbose=True, limit=args.tag_limit)
        m = {r.key(): r.tags for r in recs if r.tags}
        allr = library.load(root)
        for r in allr:
            if r.key() in m:
                r.tags = m[r.key()]
        library.save(root, allr)
        recs = [r for r in library.load(root) if r.batch == batch]
        print(f"  打标签完成: 成功 {tres['ok']} / 失败 {tres['fail']}")

    # ---- 按点亮的标签排序（主依据，用户要求的） ----
    prefs = library.load_prefs(root)
    if prefs:
        sc = library.score_by_prefs(root, recs)
        ordered = sorted(recs, key=lambda r: -(
            sc.get(r.key(), (0, 0))[0] if isinstance(sc.get(r.key()), tuple)
            else sc.get(r.key(), 0)))
        n_hit = sum(1 for r in ordered
                    if (sc.get(r.key(), (0, 0))[0] if isinstance(sc.get(r.key()), tuple)
                        else sc.get(r.key(), 0)) > 0)
        print(f"\n  按你点亮的标签排序：{n_hit}/{len(recs)} 张命中你的偏好")
        print("  最符合你偏好的前 8 个:")
        for i, r in enumerate(ordered[:8], 1):
            v = sc.get(r.key())
            s0 = v[0] if isinstance(v, tuple) else (v or 0)
            hits = []
            for dim, vals in (r.tags or {}).items():
                for t in vals:
                    if t in (prefs.get(dim) or {}):
                        hits.append(t)
            print(f"    {i:2d}. 命中 {'/'.join(hits[:4]) or '无'}"
                  f"  分 {s0:.1f}  {r.keyword[:18]}")
    elif model and len(recs) >= 5:
        # 没点亮过标签，但有特征模型 -> 退回用模型排
        pairs = preflearn.rank_batch(root, recs, model)
        preflearn.save_predictions(root, pairs)
        pmap = dict(pairs)
        ordered = sorted(recs, key=lambda r: -pmap.get(r.key(), 0.5))
        print(f"\n  按已学的特征模型排序（还没点亮过标签）:")
        for i, r in enumerate(ordered[:8], 1):
            print(f"    {i:2d}. 预测 {pmap.get(r.key(), 0):.0%}  {r.keyword[:22]}")
    else:
        print("\n  还没有偏好（去 tagpick 点亮标签），本批按原始顺序")

    batch_dir = out_root / batch
    html = labeler.build([{
        "file": r.file, "rel": r.rel, "idx": i + 1, "keyword": r.keyword,
        "source": r.source, "title": r.title,
        "pred": next((p for k, p in
                      (preflearn.rank_batch(root, [r], model) if model else [])), -1),
    } for i, r in enumerate(ordered)], batch, batch_dir / "label.html", batch_dir,
        title=f"打标签 · {batch}")

    print()
    print("=" * 78)
    print(f"  本批 {len(imgs)} 张，入库新增 {n_new} 张")
    print(f"  打标签页: {html}")
    print("=" * 78)
    print()
    print("  下一步：浏览器打开上面那个 label.html，然后：")
    print("    1/G = 好   2/B = 坏   0/S = 跳过   ←→ 切换   空格 放大   Enter 导出")
    print("  把导出的 json 存文件，再跑：")
    print(f"    python -m shein_scout patternlabel <那个json>")
    return 0


def cmd_patternlabel(args) -> int:
    """导入打标签的 json，写回图案库。"""
    from . import library, preflearn

    root = Path(args.root).resolve()
    src = Path(args.file)
    if not src.exists():
        print(f"  文件不存在: {src}")
        return 1
    try:
        rows = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  读取失败: {type(e).__name__}: {e}")
        return 1
    if not isinstance(rows, list):
        rows = rows.get("labels") or []
    if not rows:
        print("  文件里没有标签")
        return 1

    # 打标签导出的可能带 used/note（图案库页面导出的）
    n = preflearn.append_labels(root, rows)
    st = library.stats(root)
    print(f"  导入 {len(rows)} 条")
    print(f"  图案库: 共 {st['total']}（好 {st['good']} / 坏 {st['bad']} / "
          f"已用 {st['used']}）")
    if st["good"] + st["bad"] >= 12:
        print("\n  样本够了，可以学你的偏好了：")
        print("    python -m shein_scout patternlearn")
    else:
        print(f"\n  再标 {12 - st['good'] - st['bad']} 条（好+坏）就能开始学。")
    return 0


def cmd_patternlearn(args) -> int:
    """学你的偏好排序。"""
    from . import library, preflearn

    root = Path(args.root).resolve()
    print("=" * 78)
    print("学你的偏好（用于给新一批图排序）")
    print("=" * 78)
    st = library.stats(root)
    print(f"  图案库: 共 {st['total']}，其中好 {st['good']} / 坏 {st['bad']}")
    res = preflearn.learn(root, verbose=True)
    if not res.get("ok"):
        print(f"\n  {res.get('msg')}")
        return 1
    print()
    print("=" * 78)
    cv = res.get("cv") or []
    if cv:
        m = sum(c["acc"] for c in cv) / len(cv)
        print(f"  留一批验证: {m:.0%} —— 这是对新图的有效数字")
        if m < 0.65:
            print("  还偏弱，再标 1~2 批通常能明显提升。")
        else:
            print("  可以用它给新图排序了：下次 patterncrawl 会自动按偏好排。")
    else:
        print("  只有 1 个批次，无法验证泛化。多标一批更有说服力。")
    print("=" * 78)
    return 0


def cmd_pick(args) -> int:
    """图片点选：直接看图，点你喜欢的。"""
    from . import library, picker, preflearn

    root = Path(args.root).resolve()
    recs = library.load(root)
    if not recs:
        print("  图案库是空的。先跑 patterncrawl 爬一批。")
        return 1

    # 默认只显示还没选过的，避免重复劳动
    if args.batch:
        recs = [r for r in recs if r.batch == args.batch]
    if not args.all:
        recs = [r for r in recs if not r.label]
    if not recs:
        print("  没有需要点选的图（都标过了）。加 --all 可以重新看全部。")
        return 1

    model = preflearn.load_model(root)
    out = root / "runs" / "图案库" / "pick.html"
    html = picker.build(root, out, recs=recs, max_n=args.max,
                        rank=args.rank and model is not None,
                        title=args.title or "点选你喜欢的图案")

    st = library.stats(root)
    n_show = sum(1 for r in recs
                 if library.img_path(root, r.batch, r.rel, r.file).exists())
    print("=" * 78)
    print("图片点选")
    print("=" * 78)
    print(f"  图案库共 {st['total']} 张：已选 {st['good']} / 排除 {st['bad']} / "
          f"未标 {st['unlabeled']}")
    print(f"  本次显示 {n_show} 张")
    if args.rank and model:
        print("  已按你之前的选择排序（相似的排前面）")
    elif args.rank:
        print("  想按偏好排序但还没有模型 —— 先点选一批并跑 patternlearn")
    print()
    print(f"  打开: {html}")
    print()
    print("  操作：单击 = 喜欢   Shift+单击 = 不要   再点一次取消")
    print("  选完点「导出选择」-> 存成 json -> 跑：")
    print("    python -m shein_scout patternlabel <那个json>")
    if getattr(args, "do_open", False):
        try:
            import webbrowser
            webbrowser.open(html.resolve().as_uri())
            print("\n  已在浏览器打开。")
        except Exception as e:
            print(f"\n  自动打开失败（{type(e).__name__}），请手动打开上面的路径")
    return 0



def cmd_init(args) -> int:
    """初始化目录结构（迁移后首次运行用）。"""
    from . import paths
    print("=" * 70)
    print("初始化目录结构")
    print("=" * 70)
    print(paths.describe())
    print()
    r = paths.ensure_layout(verbose=True)
    print()
    print(f"  新建目录 {len(r['dirs'])} 个，新建文件 {len(r['files'])} 个")
    print("  ✓ 完成。可以运行: python -m shein_scout doctor")
    return 0


def cmd_titlegen(args) -> int:
    """生成 SHEIN 风格标题 + 上架标签。"""
    from . import titlecmd
    return titlecmd.cmd_titlegen(args)


def cmd_junk(args) -> int:
    """扫描/清理废图（水印、重复、太小）。"""
    from . import junkcmd
    return junkcmd.cmd_junk(args)


def cmd_tagctl(args) -> int:
    """用**受控词表**给图案库打标签（work/标签词表.json）。"""
    from . import tagctl
    limit = getattr(args, "limit", 0) or 0
    redo = getattr(args, "redo", False)
    import os
    if not os.environ.get("ARK_API_KEY"):
        print("需要设置 ARK_API_KEY（用豆包打标签）")
        return 1
    return tagctl.run(root=ROOT, limit=limit, redo=redo)

def cmd_tag(args) -> int:
    """用 AI 给图案库里的图打标签。"""
    from . import library, tagger

    root = Path(args.root).resolve()
    recs = library.load(root)
    if not recs:
        print("  图案库是空的。先跑 patterncrawl。")
        return 1
    if args.batch:
        recs = [r for r in recs if r.batch == args.batch]
    if args.unlabeled:
        recs = [r for r in recs if not r.tags]
    if not recs:
        print("  没有需要打标签的图。")
        return 1
    if not os.environ.get("ARK_API_KEY"):
        print("  需要设置 ARK_API_KEY（用豆包打标签）")
        return 1

    print("=" * 78)
    print("AI 打标签")
    print("=" * 78)
    print(f"  目标 {len(recs)} 张")
    # recs 是 load 出来的对象，tag_many 会就地写入 r.tags。
    # 但要覆盖「--batch / --unlabeled / --limit 筛过的那部分」，
    # 所以要重新 load 全量、把筛过的那些对象的 tags 搬过去，再整体保存。
    res = tagger.tag_many(recs, root, verbose=True, limit=args.limit)
    if res["ok"]:
        tagged = {r.key(): r.tags for r in recs if r.tags}
        allr = library.load(root)
        for r in allr:
            if r.key() in tagged:
                r.tags = tagged[r.key()]
        library.save(root, allr)
    print()
    print(f"  成功 {res['ok']} / 失败 {res['fail']}")
    if res["ok"]:
        print("  下一步：python -m shein_scout tagpick   （点亮你喜欢的标签）")
    return 0


def cmd_tagpick(args) -> int:
    """生成标签点亮页面 —— 用户点喜欢的标签，成为审美权重表。"""
    from . import library, tagpick

    root = Path(args.root).resolve()
    recs = library.load(root)
    tagged = [r for r in recs if r.tags]
    if not tagged:
        print("  还没有打过标签的图。先跑：python -m shein_scout tag")
        return 1
    out = root / "runs" / "图案库" / "tagpick.html"
    html = tagpick.build(root, out)
    stats = library.all_tags(root)
    n_tag = sum(len(v) for v in stats.values())
    print("=" * 78)
    print("标签点亮页")
    print("=" * 78)
    print(f"  已打标签的图 {len(tagged)} 张，共 {len(stats)} 个维度、{n_tag} 个标签")
    for d, vals in stats.items():
        top = list(vals.items())[:5]
        print(f"    {d:8s} {len(vals):2d} 个标签   例: "
              + ", ".join(f"{k}({v})" for k, v in top))
    print()
    print(f"  打开: {html}")
    print("  点亮你喜欢的标签 -> 导出 -> python -m shein_scout tagapply <json>")
    return 0


def cmd_tagapply(args) -> int:
    """应用点亮的标签（写回偏好），并可选按偏好给库里所有图重排序。"""
    from . import library

    root = Path(args.root).resolve()
    f = Path(args.file)
    if not f.exists():
        print(f"  文件不存在: {f}")
        return 1
    try:
        picked = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  读取失败: {e}")
        return 1
    prefs = library.set_prefs(root, picked)
    n = sum(len(v) for v in prefs.values())
    print(f"  已保存偏好：{n} 个标签")
    for d, vals in prefs.items():
        print(f"    {d}: {', '.join(vals.keys())}")
    print("\n  下次 patterncrawl 会自动按这些偏好给新图排序。")
    return 0


def cmd_library(args) -> int:
    """生成图案库页面 + 导出。"""
    from . import library, libraryview

    root = Path(args.root).resolve()
    st = library.stats(root)
    if st["total"] == 0:
        print("  图案库是空的。先跑 patterncrawl 爬一批。")
        return 1

    print("=" * 78)
    print("图案库")
    print("=" * 78)
    print(f"  共 {st['total']} 张：好 {st['good']} / 坏 {st['bad']} / "
          f"跳过 {st['skip']} / 未标 {st['unlabeled']}")
    print(f"  已用 {st['used']} 张 · {st['batches']} 个批次 · "
          f"{st['keywords']} 个关键词")

    out_dir = root / "runs" / "图案库"
    html = libraryview.build(root, out_dir / "library.html", title="图案库")
    print(f"\n  页面: {html}")

    if args.export_good:
        d = library.export_good(root, out_dir / "好的图案",
                                copy_files=args.copy_files)
        print(f"  导出「好」的图案: {d['n']} 张"
              + (f"，复制文件 {d['copied']} 个" if args.copy_files else ""))
        print(f"  清单: {d['csv']}")
    if args.export_all:
        c = library.export_csv(root, out_dir / "图案库全部.csv")
        print(f"  导出全部清单: {c}")
    if args.mark_used:
        n = library.mark_used(root, args.mark_used, used=True)
        print(f"  标记已用: {n} 张")
    if args.note:
        keys, _, note = args.note.partition("=")
        n = library.annotate(root, [k.strip() for k in keys.split(",")], note)
        print(f"  加备注: {n} 张")

    print("\n  页面用法：按标签/关键词/备注筛选，点「标记已用」「备注」，")
    print("          改完点「导出变更」把 json 发我，我写回库。")
    return 0


def cmd_run(args) -> int:
    root = Path(args.root).resolve()
    if args.crawl:
        rc = cmd_crawl(argparse.Namespace(root=str(root), query=args.crawl,
                                          out=args.out, limit=args.limit,
                                          min_side=args.min_side, pages=args.pages,
                                          sources=args.sources,
                                          allow_stock=args.allow_stock,
                                          dry_run=False))
        if rc != 0:
            return rc
    targets = [args.out] if args.out else ["samples/过审", "samples/被刷", "pool/网络候选"]
    cmd_score(argparse.Namespace(root=str(root), paths=targets))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m shein_scout",
        description="SHEIN 午休毯 · 图片风格筛查工具（合规检查 + 过审风格画像 + 网络候选筛选）")
    ap.add_argument("--root", default=".", help="工作目录（默认当前目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="体检：环境、图源、样本齐不齐")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("crawl", help="【归档】前期：商品爬虫（被 patterncrawl 取代）")
    p.add_argument("query", help="搜索词，如 \"午休毯 白底 产品图\"")
    p.add_argument("-n", "--limit", type=int, default=60, help="最多下载张数（默认 60）")
    p.add_argument("--out", default=None, help="输出目录（默认 pool/网络候选）")
    p.add_argument("--pages", type=int, default=2, help="Bing 翻页数（默认 2）")
    p.add_argument("--sources", default="bing,360",
                   help="图源，逗号分隔：bing,360,pexels,pixabay")
    p.add_argument("--min-side", type=int, default=400, help="最小边（默认 400px）")
    p.add_argument("--allow-stock", action="store_true",
                   help="允许下载带水印的付费图库图（默认禁止）")
    p.add_argument("--dry-run", action="store_true", help="只列链接不下载")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("build", help="【归档】前期：诊断画像构建（结论已否证）")
    p.add_argument("--force", action="store_true",
                   help="即使新画像明显弱于现有画像也强行覆盖")
    p.add_argument("--all-files", action="store_true",
                   help="不过滤，把文件夹里所有图都算进画像（默认只学 mockup 成品图）")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("score", help="【归档】前期：过拟合诊断（结论已否证）")
    p.add_argument("paths", nargs="*", help="图片文件或目录，可多个；默认整个工作目录")
    p.add_argument("--no-ocr", action="store_true",
                   help="跳过文字识别（默认开启，用 Windows 自带 OCR 读画面文字）")
    p.add_argument("--type", dest="force_type", default="candidate",
                   choices=["candidate", "auto", "product", "design"],
                   help="评估口径：candidate=按产品展示图评并标出疑似设计稿（默认，"
                        "用于筛网图）；product=强制全按产品展示图评；"
                        "design=强制全按印花图案稿评；auto=自动分型")
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("fix", help="【归档】前期：饱和度调整（实验已否证）")
    p.add_argument("paths", nargs="*", help="图片文件或目录")
    p.add_argument("--out", default=None, help="成品图输出目录（默认 runs/<时间>/fixed）")
    p.add_argument("--no-ocr", action="store_true", help="跳过文字识别")
    p.add_argument("--all-files", action="store_true",
                   help="处理文件夹里所有图（默认只处理文件名含 mockup 的成品展示图）")
    p.add_argument("--type", dest="force_type", default="candidate",
                   choices=["candidate", "auto", "product", "design"],
                   help="评估口径（默认 candidate）")
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("season",
                       help="档期×选品联动：多市场档期 + 标签推荐 + 抓图关键词")
    p.add_argument("--markets", default="",
                   help="市场代码逗号分隔，如 SA,GB,US（默认全部）")
    p.add_argument("--year", type=int, default=0, help="年份，默认今年")
    p.add_argument("--days", type=int, default=180, help="往后看多少天（默认180）")
    p.add_argument("--sea", type=int, default=45, help="海运提前期天数")
    p.add_argument("--air", type=int, default=20, help="空运提前期天数")
    p.add_argument("--recommend", action="store_true", help="只看有标签推荐的")
    p.add_argument("--crawl-cmd", action="store_true", help="输出可直接跑的抓图命令")
    p.add_argument("--save", action="store_true", help="存成 json")
    p.set_defaults(func=cmd_season)

    p = sub.add_parser("patterncrawl", help="爬图案图 -> 入库 -> 生成打标签页")
    p.add_argument("keywords", nargs="*", help="搜索关键词，可多个")
    p.add_argument("--want", type=int, default=120, help="目标张数（默认120）")
    p.add_argument("--sources", default="360,bing", help="来源：360,bing")
    p.add_argument("--min-side", type=int, default=400, help="最小边像素（默认400）")
    p.add_argument("--name", default="",
                   help="批次文件夹名（默认自动用「日期_关键词」）")
    p.add_argument("--no-junkfilter", action="store_true",
                   help="关掉抓图后的水印自动过滤（默认开着）")
    p.add_argument("--archive", default="",
                   help="抓完归入这个存档目录（用硬链接，不占额外空间）")
    p.add_argument("--tag", action="store_true",
                   help="爬完自动用 AI 打标签（排序依据，需要 ARK_API_KEY）")
    p.add_argument("--tag-limit", type=int, default=0, help="最多打几张标签（0=全部）")
    p.set_defaults(func=cmd_patterncrawl)

    p = sub.add_parser("patternlabel", help="【归档】前期：导入打标签结果")
    p.add_argument("file", help="标签 json 文件路径")
    p.set_defaults(func=cmd_patternlabel)

    p = sub.add_parser("patternlearn", help="【归档】前期：偏好学习")
    p.set_defaults(func=cmd_patternlearn)

    p = sub.add_parser("pick", help="【归档】前期：图片点选页（被工作台取代）")
    p.add_argument("--batch", default="", help="只看某个批次")
    p.add_argument("--all", action="store_true", help="连已标过的一起显示")
    p.add_argument("--max", type=int, default=0, help="最多显示几张（0=全部）")
    p.add_argument("--rank", action="store_true", help="按已学偏好排序")
    p.add_argument("--title", default="", help="页面标题")
    p.add_argument("--open", action="store_true", dest="do_open",
                   help="生成后自动用浏览器打开")
    p.set_defaults(func=cmd_pick)

    ini = sub.add_parser("init", help="初始化目录结构（迁移后首次运行）")
    ini.set_defaults(func=cmd_init)

    tg = sub.add_parser("titlegen", help="生成 SHEIN 标题 + 上架标签")
    tg.add_argument("--batch", action="store_true", help="批量生成")
    tg.add_argument("--one", default="", help="单张图片路径")
    tg.add_argument("--show", action="store_true", help="看已生成的")
    tg.add_argument("--limit", type=int, default=0, help="只做 N 张")
    tg.add_argument("--archive", default="",
                    help="存档目录")
    tg.set_defaults(func=cmd_titlegen)

    j = sub.add_parser("junk", help="扫描/清理废图（水印、重复、太小）")
    j.add_argument("--scan", action="store_true", help="只扫描（默认）")
    j.add_argument("--clean", action="store_true", help="扫描并移到回收站")
    j.add_argument("--only", default="", help="只查某项: wm,dup,small")
    j.add_argument("--archive", default="", help="存档目录")
    j.add_argument("--limit", type=int, default=0, help="抽样 N 张")
    j.set_defaults(func=cmd_junk)

    t = sub.add_parser("tagctl",
                       help="用受控词表打标签（推荐；GUI 用这个）")
    t.add_argument("--limit", type=int, default=0, help="只打前 N 张（试跑用）")
    t.add_argument("--redo", action="store_true", help="重打已有标签的")
    t.set_defaults(func=cmd_tagctl)

    p = sub.add_parser("tag", help="【归档】前期：旧打标签（被 tagctl 取代）")
    p.add_argument("--batch", default="", help="只打某个批次")
    p.add_argument("--unlabeled", action="store_true", help="只打还没标签的")
    p.add_argument("--limit", type=int, default=0, help="最多打几张（0=全部）")
    p.set_defaults(func=cmd_tag)

    p = sub.add_parser("tagpick", help="【归档】前期：标签点亮页")
    p.set_defaults(func=cmd_tagpick)

    p = sub.add_parser("tagapply", help="【归档】前期：应用点亮标签")
    p.add_argument("file", help="标签点亮页导出的 json")
    p.set_defaults(func=cmd_tagapply)

    p = sub.add_parser("library", help="图案库：搜索 / 标记已用 / 导出")
    p.add_argument("--export-good", action="store_true", help="导出所有「好」的图案")
    p.add_argument("--copy-files", action="store_true", help="导出时复制图片文件")
    p.add_argument("--export-all", action="store_true", help="导出全部清单")
    p.add_argument("--mark-used", nargs="*", default=None, help="标记这些文件为已用")
    p.add_argument("--note", default="", help="加备注，格式 文件名=备注")
    p.set_defaults(func=cmd_library)

    p = sub.add_parser("productcrawl",
                       help="【归档】前期：商品抓取（反爬严重）")
    p.add_argument("keywords", nargs="*", help="搜索关键词，可给多个")
    p.add_argument("-n", "--pages", type=int, default=1, help="抓几页（每页约 48 个）")
    p.add_argument("--sort", default="", help="Amazon 排序：review-rank / price-asc-rank 等")
    p.add_argument("--sources", default="amazon", help="来源：amazon,360（逗号分隔）")
    p.add_argument("--top", type=int, default=0, help="只保留销量信号前 N 个")
    p.add_argument("--keep-no-image", action="store_true", help="保留没有主图的商品")
    p.set_defaults(func=cmd_productcrawl)

    p = sub.add_parser("vlm", help="【归档】前期：VLM 实验（被 tagctl 取代）")
    p.add_argument("--probe", action="store_true", help="逐个体检候选模型，找出可用的")
    p.add_argument("--image", default=None, help="单张图片路径")
    p.add_argument("--batch", default=None, help="批量：图片目录")
    p.add_argument("--model", default=None, help="指定模型名（不填用 $ARK_MODEL 或默认候选）")
    p.set_defaults(func=cmd_vlm)

    p = sub.add_parser("run", help="【归档】前期：一站式流水线")
    p.add_argument("--crawl", default=None, help="给关键词就顺便抓图")
    p.add_argument("--out", default=None)
    p.add_argument("-n", "--limit", type=int, default=60)
    p.add_argument("--min-side", type=int, default=400)
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--sources", default="bing,360")
    p.add_argument("--allow-stock", action="store_true")
    p.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    if args.cmd == "fix" and not getattr(args, "paths", None):
        args.paths = ["."]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
