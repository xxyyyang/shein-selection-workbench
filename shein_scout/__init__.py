# -*- coding: utf-8 -*-
"""
shein_scout —— SHEIN 午休毯类目图片风格筛查工具

三层判定（权重可调，全部可解释、可复查）：
  1. 硬规则合规  —— 尺寸/比例/白底/水印/清晰度等确定性判定
  2. 过审风格画像 —— 从你已过审的图统计出来的典型影调与版式，衡量"像不像"
  3. 判别模型    —— 用"过审 vs 被刷"两组样本训练的逻辑回归，学平台的刷图边界

快速开始:
    python -m shein_scout doctor
    python -m shein_scout crawl "午休毯 白底 产品图" -n 60
    python -m shein_scout build          # 需先往 samples/过审、samples/被刷 放图
    python -m shein_scout score pool/网络候选
"""
from . import features, io_utils, learn, report, score  # noqa: F401

__version__ = "0.1.0"
__all__ = ["features", "io_utils", "learn", "report", "score", "cli"]
