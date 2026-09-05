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
