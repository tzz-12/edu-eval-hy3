"""检索质量与缺陷注入闭环测试（P0-9）。

覆盖两类曾真实发生的缺陷，防止回归：
1. contains 排序：早期对所有子串匹配给固定 60 分，导致单字概念
   （线/点/面/弦/0）抢占 top1 ——「抛物线」→「线」、「余弦」→「弦」。
2. FTS bm25 方向：SQLite FTS5 的 bm25() 越负越相关，早期当作分数
   做降序排序，最相关的被排到了最后。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

KB_JSONL = os.path.join(ROOT, "data", "kb", "knowledge.jsonl")
KB_DB = os.path.join(ROOT, "data", "kb", "knowledge.db")
QUERIES = os.path.join(ROOT, "data", "testsets", "retrieval_queries.jsonl")
DEFECTS = os.path.join(ROOT, "data", "samples", "defects.jsonl")
GAPS = os.path.join(ROOT, "data", "testsets", "kb_gaps.json")

requires_kb = pytest.mark.skipif(
    not (os.path.exists(KB_DB) and os.path.exists(KB_JSONL)),
    reason="知识库未构建（运行 python -m edu_eval.kb.ingest）")


@requires_kb
class TestRetrievalRanking:
    """排序质量：长概念优先于单字概念（回归防护）。"""

    @pytest.fixture(scope="class")
    @staticmethod
    def ret():
        from edu_eval.kb.retriever import KBRetriever
        r = KBRetriever(db_path=KB_DB, jsonl_path=KB_JSONL)
        yield r
        r.close()

    @pytest.mark.parametrize("query,expected", [
        ("抛物线", "抛物线"),
        ("余弦", "余弦"),
        ("勾股定理", "勾股定理"),
    ])
    def test_exact_name_wins(self, ret, query, expected):
        hits = ret.search(query, top_k=1)
        assert hits and hits[0].name == expected

    @pytest.mark.parametrize("query,expected", [
        ("余弦值随锐角的增大而减小", "余弦"),              # 曾误命中「弦」
        ("二次函数 y=ax²+bx+c 的图象是抛物线", "二次函数"),  # 曾误命中「线」
        ("用配方法解一元二次方程 x²-4x+3=0", "一元二次方程"),  # 曾误命中「0」
    ])
    def test_long_concept_beats_single_char(self, ret, query, expected):
        """句中召回：多字概念必须压过单字概念。"""
        hits = ret.search(query, top_k=1)
        assert hits, f"「{query}」无任何召回"
        assert hits[0].name == expected

    def test_fts_score_is_positive_and_below_contains(self, ret):
        """FTS 兜底分数必须为正、且不与 contains 竞争（< 55）。"""
        for q in ["勾股定理的证明", "直角三角形的边角关系"]:
            for h in ret.search(q, top_k=5):
                if h.match_type == "fts":
                    assert h.score > 0, "FTS 分数须为正（bm25 为负，已做单调映射）"
                    assert h.score < 55, "FTS 兜底不得压过 contains 下限"

    def test_noise_query_not_recalled(self, ret):
        """逐字噪声：查询整体与初中知识无关时不应召回。"""
        for q in ["平面向量", "线性回归", "洛必达法则"]:
            hits = ret.search(q, top_k=3)
            assert not hits, f"「{q}」误召回：{[h.name for h in hits]}"


@requires_kb
class TestRetrievalTestset:
    """测试集基线：期望答案独立于检索器，指标不低于既定基线。"""

    @pytest.fixture(scope="class")
    @staticmethod
    def queries():
        if not os.path.exists(QUERIES):
            pytest.skip("检索测试集未生成（scripts/build_retrieval_testset.py）")
        return [json.loads(l) for l in open(QUERIES, encoding="utf-8")]

    def test_size_and_structure(self, queries):
        assert len(queries) >= 100, "测试集规模应 >= 100 条"
        for q in queries:
            assert {"query", "kind", "expected_ids"} <= set(q)

    def test_kinds_present(self, queries):
        kinds = {q["kind"] for q in queries}
        assert {"name", "alias", "context", "distractor"} <= kinds

    def test_baseline_metrics(self, queries):
        """基线（改动检索器后若显著下降，此测试会失败）。"""
        from edu_eval.kb.retriever import KBRetriever
        ret = KBRetriever(db_path=KB_DB, jsonl_path=KB_JSONL)
        # 注意分母：name/alias/context 类中「无期望」的条目（知识库缺口，
        # 见 data/testsets/kb_gaps.json）不参与命中率计算，否则会稀释指标、
        # 掩盖真实的检索质量问题。
        agg = {}
        for q in queries:
            exp = set(q["expected_ids"])
            hits = [h.id for h in ret.search(q["query"], top_k=3)]
            if q["kind"] == "distractor":
                ok = not hits
            else:
                if not exp:
                    continue
                ok = bool(exp & set(hits))
            s = agg.setdefault(q["kind"], [0, 0])
            s[0] += 1
            s[1] += int(ok)
        ret.close()

        rate = {k: v[1] / v[0] for k, v in agg.items()}
        assert rate["name"] >= 0.95, f"name 类 top3 命中率 {rate['name']:.0%}"
        assert rate["alias"] >= 0.95, f"alias 类 top3 命中率 {rate['alias']:.0%}"
        assert rate["context"] >= 0.80, f"context 类 top3 命中率 {rate['context']:.0%}"
        assert rate["distractor"] >= 0.70, \
            f"distractor 零召回率 {rate['distractor']:.0%}"


class TestDefectInjection:
    """缺陷注入器：记录完整性 + 规则层检出闭环。"""

    @pytest.fixture(scope="class")
    @staticmethod
    def defects():
        if not os.path.exists(DEFECTS):
            pytest.skip("缺陷样本未生成（scripts/inject_defects.py）")
        return [json.loads(l) for l in open(DEFECTS, encoding="utf-8")]

    def test_records_complete(self, defects):
        for d in defects:
            assert {"sample_id", "type", "severity",
                    "expected_admission", "expected_dims"} <= set(d)
            if d.get("injected"):
                assert d["text"], "注入样本必须带文本"
                assert d["position"], "注入样本必须记录位置"

    def test_skips_are_explained(self, defects):
        """跳过的配方必须说明原因，不能无声消失。"""
        for d in defects:
            if not d.get("injected"):
                assert d.get("skip_reason") in ("topic_mismatch",
                                                "anchor_missing")

    @pytest.mark.skipif(not os.path.exists(DEFECTS),
                        reason="缺陷样本未生成")
    @requires_kb
    def test_rule_layer_detects_deterministic_defects(self, defects):
        """规则层必须检出确定性缺陷（公式恒等错误 / 显式超纲）。"""
        detected = {d["type"] for d in defects
                    if d.get("injected") and d.get("rule_verdict") == "FAIL(新增)"}
        assert "formula_wrong" in detected, "公式错误必须被规则层判 FAIL"
        assert "grade_beyond" in detected, "显式超纲必须被规则层判 FAIL"

    @requires_kb
    def test_rule_verdict_is_relative_to_baseline(self, defects):
        """规则层判定须与基线对比（绝对判定会让所有样本都显示告警）。"""
        vals = {d.get("rule_verdict") for d in defects if d.get("injected")}
        assert vals <= {"FAIL(新增)", "WARN(新增)", "未检出"}


@requires_kb
class TestGradeJudgement:
    """年级判定分级：显式概念严格、联想概念宽松。"""

    def test_explicit_concept_beyond_is_fail(self):
        from edu_eval.eval.rules import RuleEngine
        from edu_eval.kb.grade_map import GradeMap
        from edu_eval.kb.retriever import KBRetriever

        gm = GradeMap.load(os.path.join(ROOT, "data", "kb",
                                        "concept_grade.json"))
        ret = KBRetriever(db_path=KB_DB, jsonl_path=KB_JSONL)
        engine = RuleEngine(grade_map=gm, retriever=ret)

        text = ("## 拓展\n补充：形如 ax²+bx+c=0 的一元二次方程可用求根公式 "
                "x=(-b±√(b²-4ac))/(2a) 直接求解。")
        rep = engine.evaluate(text, declared_grade="七年级", retriever=ret)
        fails = [f for f in rep["findings"]
                 if f["verdict"] == "fail" and "GRADE" in f["rule_id"]]
        assert fails, "文本显式出现「一元二次方程」，七年级设计应判超纲 FAIL"
        assert "一元二次方程" in fails[0]["evidence"]
        ret.close()

    def test_clean_sample_not_flagged_as_beyond(self):
        """抗假阳性：干净样本不得被判超纲。

        「代数式」实为七上引入，但 K12-KGraph 仅挂八下，若不豁免则
        任何提到它的七年级教案都会被误判超纲（系统性假阳性）。
        """
        from edu_eval.eval.rules import RuleEngine
        from edu_eval.kb.grade_map import GradeMap
        from edu_eval.kb.retriever import KBRetriever

        gm = GradeMap.load(os.path.join(ROOT, "data", "kb",
                                        "concept_grade.json"))
        ret = KBRetriever(db_path=KB_DB, jsonl_path=KB_JSONL)
        engine = RuleEngine(grade_map=gm, retriever=ret)
        base = open(os.path.join(ROOT, "data", "samples",
                                 "example_lesson.md"), encoding="utf-8").read()
        rep = engine.evaluate(base, declared_grade="七年级", retriever=ret)
        grade_fails = [f for f in rep["findings"]
                       if "GRADE" in f["rule_id"] and f["verdict"] == "fail"]
        assert not grade_fails, \
            f"干净样本被误判超纲：{[f['evidence'] for f in grade_fails]}"
        # 豁免项应被显式记录（可审计），而非静默丢弃
        exempt = [f for f in rep["findings"]
                  if f["rule_id"] == "R-GRADE-EXEMPT"]
        assert exempt, "豁免的概念应留痕（R-GRADE-EXEMPT），便于审计"
        ret.close()

    def test_exempt_table_has_evidence(self):
        """豁免表每条必须带教材依据，不接受无据豁免。"""
        from edu_eval.eval.rules import load_grade_exempt
        tbl = load_grade_exempt()
        assert tbl, "豁免表未加载"
        for name, e in tbl.items():
            assert e.get("reason"), f"豁免项「{name}」缺少理由"
            assert e.get("evidence"), f"豁免项「{name}」缺少教材依据"
            assert e.get("status") == "confirmed", \
                f"豁免项「{name}」状态未经确认"

    def test_explicit_and_associated_are_separate_findings(self):
        from edu_eval.eval.rules import RuleEngine
        from edu_eval.kb.grade_map import GradeMap
        from edu_eval.kb.retriever import KBRetriever

        gm = GradeMap.load(os.path.join(ROOT, "data", "kb",
                                        "concept_grade.json"))
        ret = KBRetriever(db_path=KB_DB, jsonl_path=KB_JSONL)
        engine = RuleEngine(grade_map=gm, retriever=ret)
        fs = engine.check_grade_by_text(
            "复习有理数的加减法，然后讲一元二次方程", "七年级", ret)
        ids = {f.rule_id for f in fs}
        assert "R-GRADE-EXPL" in ids, "显式概念应有独立判定项"
        ret.close()
