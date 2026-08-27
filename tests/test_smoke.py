"""冒烟测试：不依赖 Hy3 密钥即可运行（使用演示模式）。"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

from edu_eval.eval import dimensions as D  # noqa: E402
from edu_eval.eval.aggregator import aggregate  # noqa: E402
from edu_eval.parse.parsers import parse_file  # noqa: E402
from edu_eval.config import Hy3Config  # noqa: E402
from edu_eval.eval.run_eval import EvalContext, evaluate  # noqa: E402


def test_weights_sum_to_100():
    D.validate_weights()
    assert abs(D.weighted_total_weights() - 100.0) < 1e-6


def test_dimension_count():
    assert len(D.DIMENSIONS) == 10  # G0 + 8 加权 + 1 辅助


def test_parse_markdown():
    sample = os.path.join(ROOT, "data", "samples", "example_lesson.md")
    doc = parse_file(sample)
    assert doc.parse_status == "ok"
    assert "一元一次方程" in doc.text


def test_aggregate_fail():
    res = aggregate("FAIL", False, {})
    assert res["verdict"] == "不通过"


def test_aggregate_pass():
    scores = {d.id: {"score": 4, "evidence": "x", "ne": False}
              for d in D.DIMENSIONS if d.in_total}
    res = aggregate("PASS", False, scores)
    assert res["total_score"] is not None
    assert res["verdict"] in ("通过", "待改进")


def test_evaluate_mock():
    os.environ["HY3_MOCK"] = "1"
    cfg = Hy3Config.from_env(require_key=False)
    sample = os.path.join(ROOT, "data", "samples", "example_lesson.md")
    ctx = EvalContext(grade="七年级", version="人教版", topic="一元一次方程", period="1课时")
    report = evaluate(sample, cfg, ctx)
    assert report.admission in ("PASS", "FAIL", "NE")
    assert "scores" in report.to_dict()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
