"""规则层硬证据注入 Judge 的闭环测试（P0-9）。

背景：早期实现里规则层 findings 只被写进 report，没有传给 Judge，
导致维度 3（学段适配）的评分完全依赖 LLM —— 规则层已经算出的
超纲结论被白白浪费。本组测试保证这条证据链不再断掉。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

KB_DB = os.path.join(ROOT, "data", "kb", "knowledge.db")
KB_JSONL = os.path.join(ROOT, "data", "kb", "knowledge.jsonl")
GRADE_JSON = os.path.join(ROOT, "data", "kb", "concept_grade.json")

requires_kb = pytest.mark.skipif(
    not (os.path.exists(KB_DB) and os.path.exists(GRADE_JSON)),
    reason="知识库未构建")


def _engine():
    from edu_eval.eval.rules import RuleEngine
    from edu_eval.kb.grade_map import GradeMap
    from edu_eval.kb.retriever import KBRetriever
    gm = GradeMap.load(GRADE_JSON)
    ret = KBRetriever(db_path=KB_DB, jsonl_path=KB_JSONL)
    return RuleEngine(grade_map=gm, retriever=ret), ret


@requires_kb
def test_evidence_includes_beyond_grade_finding():
    """超纲判定必须出现在注入 Judge 的证据文本里。"""
    from edu_eval.eval.orchestrator import Orchestrator
    eng, ret = _engine()
    text = ("## 拓展\n补充：形如 ax²+bx+c=0 的一元二次方程可用求根公式 "
            "x=(-b±√(b²-4ac))/(2a) 直接求解。")
    ev = Orchestrator._format_rule_evidence(
        eng.evaluate(text, declared_grade="七年级", retriever=ret))
    assert "超纲" in ev and "一元二次方程" in ev
    ret.close()


@requires_kb
def test_evidence_marks_exempt_concepts():
    """豁免概念必须留痕，让 Judge 知道不得据此扣分。"""
    from edu_eval.eval.orchestrator import Orchestrator
    eng, ret = _engine()
    base = open(os.path.join(ROOT, "data", "samples", "example_lesson.md"),
                encoding="utf-8").read()
    ev = Orchestrator._format_rule_evidence(
        eng.evaluate(base, declared_grade="七年级", retriever=ret))
    assert "豁免" in ev and "代数式" in ev
    # 精确匹配判定行：豁免说明里也含"超纲"二字，不能简单搜子串
    assert not any(ln.startswith("- 超纲（") for ln in ev.splitlines()), \
        f"干净样本不应产生超纲证据：\n{ev}"
    ret.close()


@requires_kb
def test_evidence_is_empty_for_no_signal():
    """无年级信息时证据应为空（不占用提示预算、不误导 Judge）。"""
    from edu_eval.eval.orchestrator import Orchestrator
    eng, ret = _engine()
    ev = Orchestrator._format_rule_evidence(
        eng.evaluate("随便一段没有数学概念的文字。",
                     declared_grade="七年级", retriever=ret))
    assert ev.strip() == "" or "判定不可用" in ev
    ret.close()


def test_cache_key_varies_with_rule_evidence():
    """规则证据必须参与缓存键：同一正文不同判定不得共用缓存。"""
    from edu_eval.eval.cache import cache_key
    k1 = cache_key("同一段正文", "fact", 0.0, "m", "")
    k2 = cache_key("同一段正文", "fact", 0.0, "m", "- 超纲：一元二次方程")
    assert k1 != k2, "规则证据未参与哈希，会导致不同判定串味"


def test_fact_prompt_contains_rule_block():
    """事实 Judge 的提示里必须出现规则层证据块并声明其优先级。"""
    from edu_eval.eval.judges.fact import FactJudge
    from edu_eval.hy3 import Hy3Client
    from edu_eval.config import Hy3Config

    cfg = Hy3Config.from_env(require_key=False)
    j = FactJudge(Hy3Client(cfg), cache=None)
    prompt = j.build_user_prompt(
        "正文", {"grade": "七年级"}, kb_context="知识库",
        rule_evidence="- 超纲（概念在原文中显式出现）：「一元二次方程」属 九年级上册")
    assert "规则层确定性判定" in prompt
    assert "优先级最高" in prompt
    assert "一元二次方程" in prompt
    assert "维度 3" in prompt, "应明确约束维度 3 的评分上限"


def test_fact_prompt_omits_rule_block_when_empty():
    """无规则证据时不应插入空块（避免提示噪声）。"""
    from edu_eval.eval.judges.fact import FactJudge
    from edu_eval.hy3 import Hy3Client
    from edu_eval.config import Hy3Config

    cfg = Hy3Config.from_env(require_key=False)
    j = FactJudge(Hy3Client(cfg), cache=None)
    prompt = j.build_user_prompt("正文", {"grade": "七年级"})
    assert "规则层确定性判定" not in prompt
