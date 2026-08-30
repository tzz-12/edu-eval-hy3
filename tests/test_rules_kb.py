"""Phase 0 规则层与知识库测试（M1 里程碑）。

运行（仓库根目录）：PYTHONPATH=src pytest tests/test_rules_kb.py -v
依赖 data/kb/ 索引（python -m edu_eval.kb.ingest 先行生成）。
"""
import json
import os

import pytest

from edu_eval.eval.rules import (
    RuleEngine,
    extract_equations,
    normalize_math,
    verify_equation,
)
from edu_eval.kb.grade_map import GradeMap, normalize_declared_grade
from edu_eval.kb.schema import Tier1Entry, Tier2Entry

KB_JSONL = "data/kb/knowledge.jsonl"
KB_DB = "data/kb/knowledge.db"
GRADE_JSON = "data/kb/concept_grade.json"
CURR_JSONL = "data/kb/curriculum_junior.jsonl"

requires_kb = pytest.mark.skipif(
    not os.path.exists(KB_JSONL), reason="先运行 python -m edu_eval.kb.ingest")


# ---------------- M1：公式核验 ----------------

class TestFormulaVerification:
    @pytest.mark.parametrize("raw", [
        "(a+b)²=a²+b²",          # 完全平方错误（漏 2ab）
        "(x+3)²=x²+9",           # 同型错误
        "(a+b)(a-b)=a²-b²",      # 正确：平方差
    ])
    def test_extract(self, raw):
        text = f"教学环节中板书 {raw}，学生练习。"
        assert raw in extract_equations(text)

    def test_wrong_identity_fail(self):
        chk = verify_equation("(a+b)²=a²+b²")
        assert chk.verdict == "fail"
        assert chk.counterexample is not None  # 反例可追溯

    def test_correct_identity_pass(self):
        for eq in ["(a+b)²=a²+2ab+b²", "(a+b)(a-b)=a²-b²",
                   "a²-b²=(a+b)(a-b)", "(a-b)²=a²-2ab+b²"]:
            assert verify_equation(eq).verdict == "pass", eq

    def test_numeric_equation_pass(self):
        # 数值等式（非恒等式）：3+4=7 成立
        assert verify_equation("3+4=7").verdict == "pass"
        assert verify_equation("3+4=8").verdict == "fail"

    def test_unparseable_ne(self):
        # 不可解析的碎片 → NE（保守判定，不猜测）
        assert verify_equation("中文=无法解析").verdict == "ne"

    def test_normalize(self):
        assert normalize_math("（a＋b）²") == "(a+b)**2"
        assert normalize_math("√(a²)") == "sqrt(a**2)"
        assert normalize_math("2×3=6") == "2*3=6"


# ---------------- M1：年级映射（维度 3） ----------------

class TestGradeMap:
    def test_normalize_declared(self):
        assert normalize_declared_grade("初一") == {"七年级上册", "七年级下册"}
        assert normalize_declared_grade("8年级上册") == {"八年级上册"}
        assert normalize_declared_grade("九年级") == {"九年级上册", "九年级下册"}
        assert normalize_declared_grade("高一") == set()  # 高中 → 不可解析
        assert normalize_declared_grade("") == set()

    @requires_kb
    def test_beyond_scope(self):
        gm = GradeMap.load(GRADE_JSON)
        x2 = next(c for c, v in gm.mapping.items() if v["name"] == "一元二次方程")
        assert gm.within(x2, "九年级") is True
        assert gm.within(x2, "七年级") is False
        assert gm.within(x2, "未知概念") is None

    @requires_kb
    def test_full_coverage(self):
        gm = GradeMap.load(GRADE_JSON)
        mapped = sum(1 for v in gm.mapping.values() if v["grades"])
        assert mapped == len(gm.mapping) == 451  # 100% 可映射


# ---------------- M1：规则层集成 ----------------

BAD_DOC = """# 因式分解教学设计（八年级上册）
## 教学目标
理解平方差公式。
## 教学重点
公式结构特征。
## 教学过程
板书 (a+b)(a-b)=a²+b² 供学生观察。
## 作业布置
习题 14.3。
"""

GOOD_DOC = """# 因式分解教学设计（八年级上册）
## 教学目标
理解平方差公式。
## 教学重点
公式结构特征。
## 教学过程
板书 (a+b)(a-b)=a²-b² 供学生观察。
## 作业布置
习题 14.3。
"""


class TestRuleEngine:
    def test_m1_bad_doc_fails(self):
        """M1 里程碑：含错误公式的样本被规则层判 FAIL（无需 Hy3 Key）。"""
        rep = RuleEngine().evaluate(BAD_DOC, declared_grade="八年级")
        assert rep["g0_rule_verdict"] == "FAIL"
        assert rep["summary"]["formula_failed"] >= 1

    def test_m1_good_doc_passes(self):
        rep = RuleEngine().evaluate(GOOD_DOC, declared_grade="八年级")
        assert rep["g0_rule_verdict"] == "NE"  # 无规则层错误 → 交由 Judge
        assert rep["summary"]["formula_failed"] == 0

    def test_structure_warnings(self):
        rep = RuleEngine().evaluate("只有正文没有章节的文档", declared_grade="")
        missing = [f for f in rep["findings"]
                   if f["rule_id"] == "R-STRUCT" and f["verdict"] == "warn"]
        assert len(missing) >= 3  # 目标/重难点/过程/作业至少缺 3

    @requires_kb
    def test_grade_fail_with_evidence(self):
        gm = GradeMap.load(GRADE_JSON)
        x2 = next(c for c, v in gm.mapping.items() if v["name"] == "一元二次方程")
        rep = RuleEngine(grade_map=gm).evaluate(
            GOOD_DOC, declared_grade="七年级", concept_ids=[x2])
        fails = [f for f in rep["findings"] if f["verdict"] == "fail"]
        assert any("一元二次方程" in f["evidence"] for f in fails)


# ---------------- schema ----------------

class TestSchema:
    def test_tier1_requires_meta(self):
        assert Tier1Entry(id="a", name="n", definition="d",
                          grade="七年级上册").validate() != []
        ok = Tier1Entry(id="a", name="n", definition="d", grade="七年级上册",
                        source="s", license="l", version="v")
        assert ok.validate() == []

    def test_tier1_multi_grade(self):
        e = Tier1Entry(id="a", name="n", definition="d",
                       grade="七年级上册、八年级上册",
                       source="s", license="l", version="v")
        assert e.validate() == []  # 跨册复现合法

    def test_tier2_verified_gate(self):
        base = dict(id="as-1", topic="t", assertion="a",
                    source="s", license="l", version="v")
        assert Tier2Entry(**base,
                          verification={"status": "verified"}).is_verified
        assert not Tier2Entry(**base,
                              verification={"status": "unverified"}).is_verified
        assert not Tier2Entry(**base, quarantined=True,
                              verification={"status": "verified"}).is_verified


# ---------------- 课标结构化 ----------------

@requires_kb
class TestCurriculum:
    def test_loaded(self):
        if not os.path.exists(CURR_JSONL):
            pytest.skip("先运行 python -m edu_eval.kb.curriculum")
        records = [json.loads(l) for l in open(CURR_JSONL, encoding="utf-8")]
        assert len(records) >= 120
        sections = {r["section"] for r in records}
        assert {"内容要求", "学业要求", "教学提示"} <= sections
        # 13 课题锚点抽查
        subs = {(r["topic"], r["subtopic"]) for r in records}
        assert ("数与式", "有理数") in subs
        assert ("函数", "一次函数") in subs
        assert ("函数", "二次函数") in subs
