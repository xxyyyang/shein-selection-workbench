# -*- coding: utf-8 -*-
"""
shein_scout.ocr —— 用 Windows 自带 OCR 真实"读"出画面里的文字

为什么需要它
------------
纯图像启发式判"有没有文字"在真实照片上误报极高 —— 实测 24 张真实网图，
麻花针织纹理、深色毛毯、报纸照片、木质背景全被判成"疑似文字"，误报 23/24。
因为"短笔画 + 高对比"本来就是布料纹理的固有特征，靠像素统计区分不了。

Windows 系统自带 OCR（Windows.Media.Ocr）能真正识别字符，支持中文（实测本机
识别器为 zh-Hans-CN），而且不需要联网、不花钱。用它来判定"是不是真有字"。

降级策略：拿不到 OCR（非 Windows / 缺 winsdk）就返回 None，调用方应把
文字判定降级为"软警告"，绝不能当硬门槛 —— 宁可漏报提示，不要误判毁掉好图。
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import re
from pathlib import Path

_ENGINE = None
_ENGINE_TRIED = False
_BACKEND = None

# PowerShell 后备脚本：用 Windows 内置 OCR，不需要任何 pip 包。
# 本机实测可用，支持中文（zh-Hans-CN）—— 因为 winsdk 在 Python 3.12 上经常
# 没有预编译 wheel，装不上时这条路就是唯一选择。
PS_SCRIPT = Path(__file__).resolve().parent.parent / "win_ocr2.ps1"


def _load_backend():
    """返回 (模块, 后端名) 或 (None, None)。"""
    global _BACKEND
    if _BACKEND is not None:
        return (_BACKEND if _BACKEND != "none" else None), _BACKEND
    for name in ("winsdk.windows.media.ocr", "winrt.windows.media.ocr"):
        try:
            mod = importlib.import_module(name)
            base = name.split(".media.ocr")[0]
            _BACKEND = base
            return mod, _BACKEND
        except Exception:
            continue
    _BACKEND = "none"
    return None, None


def available() -> tuple[bool, str]:
    """检查 OCR 是否可用，返回 (是否可用, 说明)。"""
    mod, backend = _load_backend()
    if mod is not None:
        try:
            langs = mod.OcrEngine.available_recognizer_languages
            tags = [x.language_tag for x in langs]
            return True, f"{backend}，可用语言: {', '.join(tags) or '无'}"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
    if PS_SCRIPT.exists():
        return True, "PowerShell + Windows.Media.Ocr（无需 pip 包）"
    return False, "未安装 winsdk，且找不到 win_ocr2.ps1"


# --------------------------------------------------------------------------
# 后备通道：PowerShell 调用 Windows 内置 OCR
# --------------------------------------------------------------------------
def _read_text_powershell(path: str | Path, max_side: int = 1600,
                          timeout: int = 120) -> dict:
    import subprocess
    out = {"ok": False, "backend": "powershell", "text": "", "chars": 0,
           "lines": 0, "error": ""}
    if not PS_SCRIPT.exists():
        out["error"] = f"找不到脚本 {PS_SCRIPT}"
        return out
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(PS_SCRIPT), "-Path", str(Path(path).resolve()),
             "-MaxSide", str(max_side)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:150]}"
        return out
    stdout = (r.stdout or "").strip()
    if stdout.startswith("OCR_OK:"):
        text = stdout[len("OCR_OK:"):]
        # OCR 对中文常按字加空格，去掉以提高可读性
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
        text = " ".join(t.strip() for t in text.splitlines() if t.strip())
        n_chars = len(re.sub(r"\s+", "", text))
        out.update({"ok": True, "text": text, "chars": n_chars,
                    "lines": len([t for t in text.split("  ") if t.strip()]) or (1 if text else 0)})
        return out
    if stdout.startswith("OCR_NO_ENGINE"):
        out["error"] = "系统未安装 OCR 语言包"
        return out
    out["error"] = (stdout or (r.stderr or "").strip() or "未知错误")[:200]
    return out


def read_text(path: str | Path) -> dict:
    """识别图片中的文字。返回 {ok, backend, text, chars, lines, error}。

    拿不到 OCR 时 ok=False，调用方必须降级处理，不要据此判负。
    """
    mod, _ = _load_backend()
    if mod is not None:
        out = {"ok": False, "backend": _BACKEND or "", "text": "", "chars": 0,
               "lines": 0, "error": ""}
        data = _png_for_ocr(path)
        if data is None:
            out["error"] = "图片解码失败"
            return out
        if _engine() is not None:
            try:
                text = asyncio.run(_recognize_async(data))
                text = " ".join(t.strip() for t in text.splitlines() if t.strip())
                out.update({"ok": True, "text": text,
                            "chars": len(text.replace(" ", "")),
                            "lines": len([t for t in text.split("  ") if t.strip()]) or (1 if text else 0)})
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        else:
            out["error"] = "OCR 引擎创建失败"
        if out["ok"]:
            return out
        # winsdk 失败 -> 退到 PowerShell
        ps = _read_text_powershell(path)
        if ps["ok"]:
            return ps
        ps["error"] = f"winsdk: {out['error']} | powershell: {ps['error']}"
        return ps
    return _read_text_powershell(path)


def _engine():
    global _ENGINE, _ENGINE_TRIED
    if _ENGINE_TRIED:
        return _ENGINE
    _ENGINE_TRIED = True
    mod, _ = _load_backend()
    if mod is None:
        return None
    for factory in ("try_create_from_user_profile_languages",
                    "try_create_from_language"):
        try:
            fn = getattr(mod.OcrEngine, factory)
            eng = fn() if factory.endswith("languages") else None
            if eng is not None:
                _ENGINE = eng
                return _ENGINE
        except Exception:
            continue
    # 退一步：直接拿第一个可用语言
    try:
        langs = mod.OcrEngine.available_recognizer_languages
        if langs:
            _ENGINE = mod.OcrEngine.try_create_from_language(langs[0])
    except Exception:
        _ENGINE = None
    return _ENGINE


def _png_for_ocr(path: str | Path, max_side: int = 1600) -> bytes | None:
    """Windows OCR 对超大图无效，且要求解码器能识别的格式；统一转 PNG 并限尺寸。"""
    try:
        import io

        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            if max(im.size) > max_side:
                s = max_side / max(im.size)
                im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))),
                               Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


async def _recognize_async(data: bytes) -> str:
    import tempfile
    mod, _ = _load_backend()
    if mod is None:
        return ""
    from winsdk.windows.graphics.imaging import BitmapDecoder  # type: ignore
    from winsdk.windows.storage import StorageFile  # type: ignore
    from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream  # type: ignore

    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    eng = _engine()
    if eng is None:
        return ""
    result = await eng.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)


def enrich(rows: list[dict], cache_path: str | Path | None = None,
           verbose: bool = True) -> dict:
    """给特征行补上 OCR 字段（原地修改）。带磁盘缓存，避免重复识别。"""
    import json
    cache: dict[str, dict] = {}
    cp = Path(cache_path) if cache_path else None
    if cp and cp.exists():
        try:
            cache = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    stat = {"total": len(rows), "ocr_ok": 0, "with_text": 0, "cached": 0, "failed": 0}
    for i, r in enumerate(rows, 1):
        if not r.get("ok"):
            continue
        fp = r.get("file")
        res = None
        if fp:
            try:
                st = Path(fp).stat()
                key = f"{Path(fp).name}|{st.st_size}|{int(st.st_mtime)}"
            except Exception:
                key = str(fp)
            res = cache.get(key)
            if res is not None:
                stat["cached"] += 1
        if res is None:
            res = read_text(fp)
            if fp:
                cache[key] = res
        r["ocr_ok"] = bool(res.get("ok"))
        r["ocr_text"] = (res.get("text") or "")[:400]
        r["ocr_chars"] = int(res.get("chars") or 0)
        r["ocr_error"] = res.get("error", "")
        if res.get("ok"):
            stat["ocr_ok"] += 1
            if r["ocr_chars"] >= 4:
                stat["with_text"] += 1
        else:
            stat["failed"] += 1
        if verbose and i % 25 == 0:
            print(f"    OCR {i}/{len(rows)}…")
    if cp:
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return stat


def content_hash(path: str | Path) -> str:
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        return ""
