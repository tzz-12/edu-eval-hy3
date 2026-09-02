"""P1-1 Tier2 断言集测试：校验器分派、三态语义、解析容错。

重点覆盖首轮实测踩到的坑：
- 「复核未返回结论」必须是 unverified（NE），不能是 quarantined（判错）
- 1-based idx 偏移要能自动纠正
- 错误断言必须被 sympy 抓出反例（quarantined）
"""
from __future__ import annotations

import json

import pytest

from edu_eval.eval.knowledge_base import _adapt
from edu_eval.eval.orchestrator import resolve_admission
from edu_eval.kb import assertions as A


# ---------------------------------------------------------------- 校验分派
def test_identity_correct_verified():
    _, status, _ = A.verify_candidate(
        {"kind": "identity", "formula": "(a+b)**2 = a**2+2*a*b+b**2"})
    assert status == "verified"


def test_identity_wrong_quarantined():
    """漏交叉项：必须被抓出反例并隔离，绝不能放行。"""
    _, status, detail = A.verify_candidate(
        {"kind": "identity", "formula": "(a+b)**2 = a**2+b**2"})
    assert status == "quarantined"
    assert "反例" in detail


def test_solve_correct_and_wrong():
    ok = A.verify_candidate(
        {"kind": "solve", "formula": "x**2-5*x+6 = 0", "solutions": ["2", "3"]})
    assert ok[1] == "verified"
    bad = A.verify_candidate(
        {"kind": "solve", "formula": "x**2-5*x+6 = 0", "solutions": ["1", "5"]})
    assert bad[1] == "quarantined"


def test_solve_missing_solutions_is_unverified():
    _, status, _ = A.verify_candidate(
        {"kind": "solve", "formula": "x**2-4 = 0", "solutions": []})
    assert status == "unverified"


def test_structural_goes_to_consensus():
    method, status, _ = A.verify_candidate(
        {"kind": "structural", "formula": "", "assertion": "SSS 可判定全等"})
    assert method == A.METHOD_NONE and status == "unverified"


def test_conditional_equation_not_treated_as_identity():
    """2x+3=11 是条件等式而非恒等式，不得据此判公式错误。"""
    _, status, _ = A.verify_candidate(
        {"kind": "identity", "formula": "2*x+3 = 11"})
    assert status == "unverified"


# ---------------------------------------------------------------- 三态语义
def test_missing_verdict_is_unverified_not_quarantined():
    """复核缺结论 = 未判定（NE），绝不等于判错。"""
    method, status, detail = A.apply_consensus({"assertion": "x"}, None)
    assert method == A.METHOD_CONSENSUS
    assert status == "unverified"
    assert "NE" in detail


def test_verdict_true_and_false():
    assert A.apply_consensus({"a": 1}, (True, "复核通过"))[1] == "verified"
    assert A.apply_consensus({"a": 1}, (False, "复核否决"))[1] == "quarantined"


# ---------------------------------------------------------------- 解析容错
def test_parse_verdicts_shifts_one_based_index():
    raw = json.dumps({"verdicts": [
        {"idx": 1, "correct": True, "reason": "r1"},
        {"idx": 2, "correct": False, "reason": "r2"},
    ]})
    got = A.parse_verdicts(raw, n_items=2)
    assert got == {0: (True, "r1"), 1: (False, "r2")}


def test_parse_verdicts_keeps_zero_based_index():
    raw = json.dumps({"verdicts": [
        {"idx": 0, "correct": True, "reason": "r0"},
        {"idx": 1, "correct": True, "reason": "r1"},
    ]})
    assert set(A.parse_verdicts(raw, n_items=2)) == {0, 1}


def test_parse_verdicts_garbage_returns_empty():
    assert A.parse_verdicts("不是 JSON") == {}


def test_parse_candidates_tolerant():
    raw = '好的：{"assertions":[{"assertion":"a"},{"assertion":"b"}]}'
    assert len(A.parse_candidates(raw)) == 2
    assert A.parse_candidates("完全没有 JSON") == []


# ---------------------------------------------------------------- 条目构造
def test_make_entry_has_required_meta_and_validates():
    topic = A.topic_by_no(3)
    entry = A.make_entry({"assertion": "移项要变号", "common_errors": ["不变号"]},
                         topic, 7, A.METHOD_CONSENSUS, "verified", "ok")
    assert A.validate_entry(entry) == []
    assert entry["id"] == "as-03-007"
    assert entry["tier"] == 2 and entry["grade"] == "七年级上册"
    # 来源元数据三件套不可缺
    assert entry["source"] and entry["license"] and entry["version"]


def test_entry_missing_assertion_fails_validation():
    topic = A.topic_by_no(3)
    entry = A.make_entry({}, topic, 1, A.METHOD_NONE, "unverified", "")
    assert A.validate_entry(entry)


def test_lookup_all_by_name_returns_every_assertion_of_topic():
    """同一课题的多条断言 topic 相同，必须全部召回而非只取首条。"""
    from edu_eval.eval.knowledge_base import KnowledgeBase

    topic = A.topic_by_no(3)
    entries = []
    for seq in range(1, 6):
        entries.append(A.make_entry(
            {"assertion": f"断言{seq}", "concepts": ["一元一次方程"]},
            topic, seq, A.METHOD_CONSENSUS, "verified", "ok"))
    kb = KnowledgeBase([_adapt(e) for e in entries])
    hits = kb._lookup_all_by_name("一元一次方程")
    assert len(hits) == 5
    assert {h.id for h in hits} == {f"as-03-{i:03d}" for i in range(1, 6)}


def test_lookup_all_skips_unverified():
    from edu_eval.eval.knowledge_base import KnowledgeBase

    topic = A.topic_by_no(3)
    good = A.make_entry({"assertion": "对", "concepts": ["移项"]}, topic, 1,
                        A.METHOD_CONSENSUS, "verified", "ok")
    bad = A.make_entry({"assertion": "错", "concepts": ["移项"]}, topic, 2,
                       A.METHOD_CONSENSUS, "quarantined", "复核否决")
    kb = KnowledgeBase([_adapt(e) for e in (good, bad)])
    # quarantined 条目在 _adapt 后 quarantined=True，检索时必须跳过
    assert len(kb._lookup_all_by_name("移项")) == 1


@pytest.mark.parametrize(
    "scores,expected",
    [
        # 模型自述 PASS，但 G0 维度标 ne → 必须整体 NE（不得出总分）
        ({"2": {"score": 5, "ne": True, "evidence": "无法核验"}}, "NE"),
        # G0 维度缺分 → NE
        ({"2": {"ne": False}}, "NE"),
        # G0 低分 → FAIL（知识错误一票否决）
        ({"2": {"score": 1, "ne": False, "evidence": "公式错误"}}, "FAIL"),
        ({"2": {"score": 3, "ne": False}}, "FAIL"),
        # 正常通过：自述 PASS 保持不变
        ({"2": {"score": 5, "ne": False, "evidence": "可核验"}}, "PASS"),
        # 模型自述 FAIL 而维度高分：保留更严厉的 FAIL
        ({"2": {"score": 5, "ne": False}}, "FAIL"),
    ],
)
def test_resolve_admission(scores, expected):
    """准入结论必须由 G0 维度的实际判定推导，不能被模型自述带偏。"""
    declared = "FAIL" if expected == "FAIL" and scores["2"].get("score", 5) > 3 \
        else "PASS"
    assert resolve_admission(declared, scores) == expected


def test_resolve_admission_without_g0_keeps_declared():
    assert resolve_admission("PASS", {}) == "PASS"


def test_topic_lookup():
    assert A.topic_by_name("一元一次方程")["no"] == 3
    with pytest.raises(KeyError):
        A.topic_by_no(99)
