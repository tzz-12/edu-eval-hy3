"""API 层共享的路径常量。

集中定义，避免 health.py 硬编码 data/demo_reports 而 evaluate.py 又读
EDU_EVAL_DEMO_DIR，两处不一致（测试隔离时就会露馅）。
"""

from __future__ import annotations

import os
from pathlib import Path


def demo_dir() -> Path:
    """预生成演示报告目录。可用 EDU_EVAL_DEMO_DIR 覆盖（测试隔离用）。"""
    return Path(os.environ.get("EDU_EVAL_DEMO_DIR", "data/demo_reports"))


def demo_samples_dir() -> Path:
    """演示样本的**源文件**目录（即被评测的那份课件底稿）。

    demo 报告不入历史库（避免污染），所以它们的源文本不在 DB 里，
    而是直接读这份仓库内的底稿。文件随仓库分发，评审拉下来就能对照查看。
    可用 EDU_EVAL_SAMPLES_DIR 覆盖（测试隔离用）。
    """
    return Path(os.environ.get("EDU_EVAL_SAMPLES_DIR", "data/samples/demo"))
