"""review.deterministic_check 的回归测试。

核心回归点：转述型证据（含正确公式）不应被误判为「不匹配」而触发无谓仲裁；
只有真编造的公式 / 与原文毫无关系的文字才应被标问题。
"""
import pytest

from edu_eval.eval.judges.review import ReviewJudge


def _run(scores, text):
    return {p["dim"] for p in ReviewJudge.deterministic_check(scores, text)}


def test_no_evidence_flagged():
    scores = {"1": {"score": 4, "evidence": ""}}
    assert _run(scores, "任意原文") == {"1"}


def test_ne_skipped_even_without_evidence():
    scores = {"2": {"ne": True, "evidence": ""}}
    assert _run(scores, "任意原文") == set()


def test_paraphrased_formula_present_not_flagged():
    # ultra-550b 典型转述证据：公式在原文、但整段不是连续子串
    text = "二次函数顶点形式记作 y=a(x-h)+k，其图像可由 y=ax 平移得到。"
    scores = {
        "2": {"score": 4, "evidence": "顶点形式记为 y=a(x-h)+k，其中系数 a 控制开口"},
        "8": {"score": 5, "evidence": "本节课完整覆盖了平移研究方法"},
    }
    assert _run(scores, text) == set()


def test_fabricated_formula_flagged():
    text = "二次函数图像与性质。"
    scores = {
        "2": {"score": 4, "evidence": "满足关系 y=z(x-h)^9+π 即可判定顶点"},
    }
    assert _run(scores, text) == {"2"}


def test_pure_text_high_coverage_not_flagged():
    text = "本节课讲解了二次函数的图像性质，并通过画函数图像探究平移关系。"
    scores = {
        "1": {"score": 5, "evidence": "本节课完整讲解了二次函数的图像性质与平移探究"},
    }
    assert _run(scores, text) == set()


def test_pure_text_paraphrase_always_tolerated():
    # 设计决策：纯文字证据容忍转述，不触发仲裁（即使与原文共享字符很少）。
    # 真正的幻觉风险点是「编造的公式」，已由数学跨度分支捕获；
    # 纯文字编造风险较低，且由「分数分歧→跨层仲裁」兜底。
    text = "二次函数图像与性质平移关系。"
    scores = {
        "1": {"score": 5, "evidence": "量子纠缠态的叠加原理在本文中得到严格推导"},
    }
    assert _run(scores, text) == set()
