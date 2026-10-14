# -*- coding: utf-8 -*-
"""
shein_scout.vlm —— 视觉模型接口（火山方舟 / 豆包）

为什么需要它
------------
本地特征能算色彩、构图、文字，但算不出"布料质感、摆放方式、材质呈现"这类语义。
而你的驳回理由正是"不符合平台审美"——这类判断需要视觉模型。

已核实的事实（来自火山方舟官方文档，见下方 doc_url）
--------------------------------------------------
* 接口地址: https://ark.cn-beijing.volces.com/api/v3/responses
* 鉴权头  : Authorization: Bearer $ARK_API_KEY
* 输入格式: input[].content[] 里放 {"type":"input_image","image_url":...}
            与 {"type":"input_text","text":...}
* **实测可用**（2026-10-09 用真实商品图验证）: `doubao-seed-evolving`
  - 实际解析到的版本: `doubao-seed-evolving-latest-version`
  - 结构化打标签 10 个字段全部正确返回
* 官方示例用的 model: "doubao-seed-2-1-pro-260628"
* 文档里出现过、支持图片输入的系列: Doubao-Seed-1.8 / Doubao-Seed-2.0 / 2.1
* 官方确认：DeepSeek-V3.2、GLM-4.7 这类**不支持**图片输入 —— 别选错

实测 token（1600px 图片 + 本文件里的打标签提示词）：
    reasoning 关闭: input ~1397  output ~179   total ~1576
    reasoning 开启: input ~1398  output ~575   total ~1973
  -> 打标签场景**务必关掉落 reasoning**（本文件已默认 `reasoning.effort="none"`）

**未核实**：具体价格与免费额度。官方价格页是 JS 渲染的静态抓不到。
请到控制台看模型详情页的单价，再用上面的 token 数自己乘一下：
    每张图成本 ≈ 0.0014 × 输入单价 + 0.00018 × 输出单价   （单位：百万 token 计价的元数）

设计要点
--------
* 模型名可配置，方便你对比不同型号
* 强制 JSON 输出（结构化字段），这样结果可统计、可复现、能算准确率
* 带磁盘缓存：同一张图不重复调用，省钱
* 不把 API key 写进代码，只从环境变量读
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

DOC_URL = "https://ai.volcengine.com/docs/ark-aihub/2646937?lang=zh"
ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/responses"

# 候选模型（按"通用多模态理解"用途排列；价格与限流请自行核实）
CANDIDATE_MODELS = [
    "doubao-seed-evolving",           # 实测可用（2026-10-09 验证），默认用它
    "doubao-seed-2-1-pro-260628",     # 官方文档示例里用的
    "doubao-seed-1-8-251228",         # 文档提到 Doubao-Seed-1.8 支持图片输入
]

# 我们关心的结构化字段 —— 固定 schema 才能统计、才能算一致率
SCHEMA_KEYS = ["背景", "摆放方式", "毯面材质观感", "主色调", "光型",
               "有人出镜", "构图角度", "画面文字", "场景类型", "整体风格一句话"]

PROMPT = """你是 SHEIN 家居纺织类目的资深买手。请分析这张印花毯商品图，只输出 JSON，不要任何解释文字。

字段与取值范围：
- 背景: 纯白/浅灰/浅色渐变/纯色/棚拍场景/家居实景/户外/其它
- 摆放方式: 平铺/卷筒/叠放/悬挂/披挂沙发/模特手持/模特披肩/床上铺展/其它
- 毯面材质观感: 绒面蓬松/绒面细腻/针织罗纹/法兰绒/羊羔绒/雪尼尔/看不清/其它
- 主色调: 米白/浅灰/莫兰迪冷/莫兰迪暖/大地色/明黄橙/红粉系/蓝绿系/黑白/多彩撞色
- 光型: 柔光棚拍/硬光棚拍/自然光/暖光氛围/混合/其它
- 有人出镜: true/false
- 构图角度: 俯拍/45度/平视/特写局部/其它
- 画面文字: 数组，列出图上所有可辨识文字（没有就空数组）
- 场景类型: 纯商品展示/生活方式场景/信息图/其它
- 整体风格一句话: 20 字以内

只输出 JSON 对象，字段名必须与上面完全一致。"""


def _cache_key(path: str | Path, model: str) -> str:
    p = Path(path)
    try:
        st = p.stat()
        return hashlib.sha1(f"{p.resolve()}|{st.st_size}|{int(st.st_mtime)}|{model}"
                            .encode()).hexdigest()
    except Exception:
        return hashlib.sha1(f"{path}|{model}".encode()).hexdigest()


def _b64_image(path: str | Path, max_side: int = 1600) -> str | None:
    """转 base64 data URI。先限尺寸，省 token 也避免超限。"""
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
            im.save(buf, "JPEG", quality=85, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def available() -> tuple[bool, str]:
    key = os.environ.get("ARK_API_KEY", "").strip()
    if not key:
        return False, "未设置 ARK_API_KEY 环境变量"
    try:
        import requests  # noqa: F401
    except ImportError:
        return False, "缺少 requests（python -m pip install requests）"
    return True, f"已就绪（key 长度 {len(key)}）"


def extract(path: str | Path, model: str | None = None, timeout: int = 90,
            cache_dir: str | Path | None = None, retries: int = 2) -> dict:
    """对单张图调用视觉模型，返回结构化字段。

    返回 {"ok", "model", "fields", "raw", "error", "cached"}
    """
    import requests

    model = model or os.environ.get("ARK_MODEL", "").strip() or CANDIDATE_MODELS[0]
    base = Path(cache_dir) if cache_dir else Path(__file__).resolve().parent.parent / "work" / "vlm_cache"
    base.mkdir(parents=True, exist_ok=True)
    ck = base / f"{_cache_key(path, model)}.json"
    if ck.exists():
        try:
            d = json.loads(ck.read_text(encoding="utf-8"))
            d["cached"] = True
            return d
        except Exception:
            pass

    out = {"ok": False, "model": model, "fields": {}, "raw": "", "error": "", "cached": False}
    key = os.environ.get("ARK_API_KEY", "").strip()
    if not key:
        out["error"] = "未设置 ARK_API_KEY"
        return out

    uri = _b64_image(path)
    if uri is None:
        out["error"] = "图片读取/编码失败"
        return out

    payload = {
        "model": model,
        # 实测：打标签不需要推理，而 reasoning 会吃掉大量 output token。
        # 关掉后 output_tokens 从 575 降到 179（省 69%），标签质量不变。
        # thinking.type="disabled" 也可用，但 reasoning.effort="none" 实测更省。
        "reasoning": {"effort": "none"},
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": uri},
                {"type": "input_text", "text": PROMPT},
            ],
        }],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    for attempt in range(retries + 1):
        try:
            r = requests.post(ENDPOINT, headers=headers, json=payload, timeout=timeout)
            if r.status_code != 200:
                out["error"] = f"HTTP {r.status_code}: {r.text[:300]}"
                if r.status_code in (401, 403, 404):
                    break           # 鉴权/模型名错误，重试无意义
                time.sleep(1.5 * (attempt + 1))
                continue
            data = r.json()
            text = _dig_text(data)
            out["raw"] = text[:4000]
            fields = _parse_json(text)
            if fields:
                out["ok"] = True
                out["fields"] = fields
            else:
                out["error"] = "模型未返回可解析的 JSON"
            break
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            time.sleep(1.5 * (attempt + 1))

    if out["ok"]:
        try:
            ck.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return out


def _dig_text(data: dict) -> str:
    """从 Responses API 的返回里挖出**答案**文本。

    已实测确认的结构（doubao-seed-evolving）：
      output 是数组，含两类元素：
        {"type":"reasoning","summary":[{"type":"summary_text","text":...}]}  <- 思考过程，跳过
        {"type":"message","content":[{"type":"output_text","text":...}]}     <- 真正的答案
    踩过的坑：不跳过 reasoning 就会把**思考过程**当成答案抓出来。
    """
    if isinstance(data, str):
        return data
    out = data.get("output")
    if isinstance(out, list):
        parts = []
        for item in out:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                continue                      # 关键：跳过思考过程
            for c in (item.get("content") or []):
                if not isinstance(c, dict):
                    continue
                t = c.get("text")
                if isinstance(t, str) and t.strip():
                    parts.append(t)
        if parts:
            return "\n".join(parts)
    for k in ("output_text",):
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v
    # OpenAI 兼容格式兜底
    ch = data.get("choices")
    if isinstance(ch, list) and ch:
        msg = ch[0].get("message") or {}
        if isinstance(msg.get("content"), str):
            return msg["content"]
    return json.dumps(data, ensure_ascii=False)[:4000]


def _parse_json(text: str) -> dict:
    """从模型输出里抽出 JSON 对象（容忍 ```json 包裹与前后废话）。"""
    if not text:
        return {}
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```")[1] if "```" in s[3:] else s[3:]
        s = s.lstrip("json").strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(s[i:j + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return {}


def extract_many(paths: list, model: str | None = None, verbose: bool = True) -> list[dict]:
    """批量处理（串行 + 缓存）。返回与输入等长的结果列表。"""
    rows = []
    for i, p in enumerate(paths, 1):
        r = extract(p, model=model)
        r["file"] = str(p)
        r["name"] = Path(p).name
        rows.append(r)
        if verbose:
            flag = "OK " if r["ok"] else "失败"
            tag = "（缓存）" if r.get("cached") else ""
            print(f"  [{i}/{len(paths)}] {flag}{tag} {Path(p).name[:40]}"
                  + ("" if r["ok"] else f"  {r['error'][:70]}"))
    return rows


def probe_models(models: list[str] | None = None, test_image: str | Path | None = None) -> dict:
    """逐个试模型名，看哪个可用 —— 用于第一次接入时确定 model id。

    用法：找一个真实图片路径，跑这个函数，看哪个模型返回 ok。
    """
    models = models or CANDIDATE_MODELS
    if test_image is None:
        return {"error": "需要 test_image 参数（一张真实图片路径）"}
    res = {}
    for m in models:
        r = extract(test_image, model=m, cache_dir=Path(__file__).resolve().parent.parent /
                     "work" / "vlm_probe")
        res[m] = {"ok": r["ok"], "error": r["error"][:200],
                  "fields": list((r.get("fields") or {}).keys())}
        print(f"  {m:36s} {'可用' if r['ok'] else '不可用'}  {r['error'][:80]}")
    return res
