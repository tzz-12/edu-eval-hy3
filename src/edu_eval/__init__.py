"""EduEval —— 基于混元（Hy3）的初中数学教学设计质量评估器。

本包为腾讯犀牛鸟开源实战「混元大语言模型」项目的个人 / 活动作品，
并非腾讯官方发布。所有模型能力通过 Hy3 完成，不训练或微调模型。
"""
from __future__ import annotations

__version__ = "0.1.0"
__author__ = "EduEval 参与者（个人 / 活动作品）"

from .config import Hy3Config
from .hy3 import Hy3Client

__all__ = ["Hy3Config", "Hy3Client", "__version__"]
