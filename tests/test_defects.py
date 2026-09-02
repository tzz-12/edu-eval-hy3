"""程序化缺陷注入器 tests（全部离线，零 LLM）。"""

import json
import os

import pytest

from edu_eval.defects import (ALL_TYPES, EXPECTED_SIGNALS, T_FORMULA,
                              T_MISSING, T_OUT_GRADE, T_PSEUDO, DefectRecord,
                              inject_all, inject_formula_error)
from edu_eval.eval.rules import RuleEngine
import random

SAMPLE = os.path.join(os.path.dirname(__file__), "..",
                      "data", "samples", "example_lesson.md")
GRADE = "七年级"


@pytest.fixture(scope="module")
def sample_text():
    with open(SAMPLE, encoding="utf-8") as f:
        return f.read()


def test_all_types_inject_clean(sample_text):
    """六类缺陷全部命中锚点，无静默 miss。"""
    res = inject_all(sample_text, GRADE, seed=42, sample_id="t1")
    got = {r.type for r in res.records}
    assert got == set(ALL_TYPES)
    assert res.misses == []
    assert res.text != sample_text


def test_determinism_same_seed(sample_text):
    a = inject_all(sample_text, GRADE, seed=7)
    b = inject_all(sample_text, GRADE, seed=7)
    assert a.text == b.text
    assert [r.detail for r in a.records] == [r.detail for r in b.records]


def test_defect_ids_unique_and_manifest_ready(sample_text):
    res = inject_all(sample_text, GRADE, seed=1, sample_id="doc9")
    ids = [r.defect_id for r in res.records]
    assert len(ids) == len(set(ids))
    for r in res.records:
        d = r.to_dict()
        assert d["expected"]["rules"] == EXPECTED_SIGNALS[d["type"]]["rules"]
        assert d["anchor"] and d["after"]


def test_formula_error_caught_by_rules_zero_llm(sample_text):
    """注入的错误恒等式必须被 R-FORMULA 判 fail（确定性判别力下限）。"""
    res = inject_all(sample_text, GRADE, types=[T_FORMULA], seed=3)
    assert res.records and res.records[0].type == T_FORMULA
    eng = RuleEngine()
    bad = [f.evidence for f in eng.check_formulas(res.text)
           if f.verdict == "fail"]
    assert any("(x+3)² = x² + 9" in e or "(a+b)² = a² + b²" in e
               for e in bad), bad


def test_missing_required_section_delta(sample_text):
    """删除必备节后，R-STRUCT 必须新增基线没有的告警（对账看增量）。"""
    eng = RuleEngine()
    base = {f.evidence for f in eng.check_structure(sample_text)}
    res = inject_all(sample_text, GRADE, types=[T_MISSING], seed=5)
    assert res.records, "锚点缺失：教学目标/作业设计应存在"
    now = {f.evidence for f in eng.check_structure(res.text)}
    new_warns = now - base
    assert new_warns, "删除必备节未产生新的 R-STRUCT 告警"
    assert any("教学目标" in w or "作业设计" in w for w in new_warns)


def test_miss_is_honest_not_silent():
    """无任何节标题的裸文本：注入器必须如实报 miss，不得改动文本。"""
    bare = "这是一段没有章节结构的纯文本，无法定位任何锚点。"
    res = inject_all(bare, GRADE, seed=9)
    assert res.text == bare
    missed = {m["type"] for m in res.misses}
    # 至少公式/推导/伪启发等节锚点类应 miss
    assert T_FORMULA in missed and T_PSEUDO in missed
    for m in res.misses:
        assert m["reason"]


def test_pseudo_heuristic_replaces_section(sample_text):
    res = inject_all(sample_text, GRADE, types=[T_PSEUDO], seed=2)
    assert len(res.records) == 1
    assert res.records[0].expected["judges"] == ["7"]
    assert "启发探究" in res.text  # 节标题保留
    assert "直接" in res.text or "公布" in res.text  # 灌输特征词进入


def test_injected_doc_shorter_after_deletion(sample_text):
    a = inject_all(sample_text, GRADE, types=[T_MISSING], seed=5)
    assert len(a.text) < len(sample_text)


def test_formula_error_without_anchor(sample_text):
    """直接调用单注入器：空文本返回 miss 而非异常。"""
    new_text, rec, miss = inject_formula_error("", random.Random(1))
    assert new_text == "" and rec is None and miss
