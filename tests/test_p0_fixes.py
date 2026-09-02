"""P0-10 修复回归测试：锁住第二轮复查发现的 6 类缺陷。

每个测试对应一个修复项，防止回归：
  D —— 前置知识（概念早于声明年级）不得判超纲
  A —— 知识库装载失败不得静默降级（Tier1/Tier2 schema 对齐）
  E —— 缓存键须覆盖渲染后的完整提示（声明年级变化不串味）
  F —— NE 维度按有效权重归一化，不当 0 分
  B —— 规则层结构化结果必须进入 Report
  G —— 知识库/缓存路径不依赖当前工作目录
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edu_eval import paths as P  # noqa: E402
from edu_eval.eval.aggregator import aggregate  # noqa: E402
from edu_eval.eval.cache import cache_key  # noqa: E402
from edu_eval.eval.knowledge_base import KnowledgeBase  # noqa: E402
from edu_eval.kb.grade_map import GradeMap, normalize_declared_grade  # noqa: E402

# ---------------------------------------------------------------- D 修复


@pytest.fixture(scope="module")
def grade_map() -> GradeMap:
    return GradeMap.load(P.grade_json())


@pytest.fixture(scope="module")
def cid_of(grade_map: GradeMap):
    def _cid(name: str) -> str:
        return next(cid for cid, v in grade_map.mapping.items()
                    if v["name"] == name)
    return _cid


class TestPrerequisiteNotBeyond:
    """D：前置知识被误判为超纲（集合交集 → 册次序号比较）。"""

    def test_earlier_concept_is_not_beyond(self, grade_map, cid_of):
        """九年级教案引用「有理数」（七上）→ earlier，不是 beyond。"""
        assert grade_map.classify(cid_of("有理数"), "九年级") == "earlier"
        assert grade_map.within(cid_of("有理数"), "九年级") is True

    def test_current_concept(self, grade_map, cid_of):
        """七年级教案引用「有理数」→ current。"""
        assert grade_map.classify(cid_of("有理数"), "七年级") == "current"

    def test_beyond_concept(self, grade_map, cid_of):
        """七年级教案引用「一元二次方程」（九上）→ beyond（真超纲）。"""
        assert grade_map.classify(cid_of("一元二次方程"), "七年级") == "beyond"
        assert grade_map.within(cid_of("一元二次方程"), "七年级") is False

    def test_monotonicity_of_beyond(self, grade_map):
        """声明年级越高，被判超纲的概念只能越少（不能变多）。"""
        all_ids = list(grade_map.mapping)
        counts = [len(grade_map.analyze(all_ids, g)["beyond"])
                  for g in ("七年级", "八年级", "九年级")]
        assert counts[0] >= counts[1] >= counts[2], \
            f"超纲数随年级升高而增加：{counts}"

    def test_analyze_partitions(self, grade_map):
        """current / earlier / beyond 三类互斥且不含同一概念。"""
        rep = grade_map.analyze(list(grade_map.mapping), "九年级")
        names = [ {i["name"] for i in rep[k]}
                  for k in ("current", "earlier", "beyond") ]
        assert not (names[0] & names[1] | names[1] & names[2] | names[0] & names[2])
        assert "有理数" not in names[2]

    def test_rule_evidence_marks_prerequisite_as_normal(self, grade_map, cid_of):
        """编排器注入 Judge 的硬证据应提示「前置知识不得判超纲」。"""
        from edu_eval.eval.rules import RuleEngine
        from edu_eval.eval.orchestrator import Orchestrator
        from edu_eval.kb.retriever import KBRetriever

        text = ("# 二次函数（九年级）\n## 教学目标\n复习有理数运算。"
                "\n## 教学重点\n重点。\n## 教学过程\n过程。\n## 作业布置\n作业。")
        ret = KBRetriever(P.kb_db(), P.kb_jsonl())
        eng = RuleEngine(grade_map=grade_map, retriever=ret)
        rep = eng.evaluate(text, declared_grade="九年级")
        ev = Orchestrator._format_rule_evidence(rep)
        # 「有理数」作为七上概念在九年级文案中出现：属前置知识
        assert "前置知识" in ev
        assert "不得据此判超纲" in ev


# ---------------------------------------------------------------- A 修复


class TestKnowledgeBaseLoad:
    """A：Tier1 概念索引必须能被 KnowledgeBase 装载（不再 TypeError）。"""

    def test_load_tier1_jsonl(self):
        kb = KnowledgeBase.load(P.kb_jsonl())
        assert len(kb.entries) >= 450  # 451 条概念
        assert all(e.tier == 1 for e in kb.entries)

    def test_retrieve_for_text_finds_concepts(self):
        kb = KnowledgeBase.load(P.kb_jsonl())
        text = "本节课学习一元一次方程的解法，涉及有理数的运算。"
        hits = kb.retrieve_for_text(text, top_k=5)
        topics = {h.topic for h in hits}
        assert "一元一次方程" in topics

    def test_load_failure_is_loud(self, tmp_path):
        """坏文件必须抛错，不得静默返回空库（0 条可用 + 有错误行 → 抛错）。"""
        bad = tmp_path / "bad.jsonl"
        bad.write_text('这不是 JSON {{\n{另一个坏行\n', encoding="utf-8")
        with pytest.raises(Exception):
            KnowledgeBase.load(str(bad))

    def test_load_safe_returns_warnings(self, tmp_path):
        """load_safe 对缺失文件返回告警而非 None 无痕。"""
        _, warns = KnowledgeBase.load_safe(str(tmp_path / "nope.jsonl"))
        assert warns and "不存在" in warns[0]


# ---------------------------------------------------------------- E 修复


class TestCacheKey:
    """E：缓存键对完整渲染提示取哈希。"""

    def test_declared_grade_changes_key(self):
        """同文本不同声明年级 → 不同键（此前会串味命中同一缓存）。"""
        k7 = cache_key("fact", 0.0, "hy3", "SYS", "USER 年级=七年级",
                       {"grade": "七年级"})
        k9 = cache_key("fact", 0.0, "hy3", "SYS", "USER 年级=九年级",
                       {"grade": "九年级"})
        assert k7 != k9

    def test_kb_context_changes_key(self):
        """知识库上下文变化 → 不同键。"""
        k1 = cache_key("fact", 0.0, "hy3", "SYS", "USER KB=甲", None)
        k2 = cache_key("fact", 0.0, "hy3", "SYS", "USER KB=乙", None)
        assert k1 != k2

    def test_context_metadata_changes_key(self):
        """即便未渲染进提示的元数据变化也换键（兜底）。"""
        k1 = cache_key("fact", 0.0, "hy3", "SYS", "USER", {"topic": "分式"})
        k2 = cache_key("fact", 0.0, "hy3", "SYS", "USER", {"topic": "勾股定理"})
        assert k1 != k2


# ---------------------------------------------------------------- F 修复


class TestAggregatorNE:
    """F：NE 维度按有效权重归一化，不得当 0 分。"""

    @staticmethod
    def _scores(all_five: bool = True, ne_dims=()):
        return {str(i): ({"score": 5, "ne": True} if str(i) in ne_dims
                         else {"score": 5, "ne": False})
                for i in range(1, 10)}

    def test_all_five_is_100(self):
        assert aggregate("PASS", False, self._scores())["total_score"] == 100

    def test_ne_not_treated_as_zero(self):
        """权重 20 的维度判 NE → 总分仍应为 100，而不是 80。"""
        from edu_eval.eval import dimensions as D

        s = self._scores(ne_dims={"1"})
        out = aggregate("PASS", False, s)
        assert out["total_score"] == 100
        assert "1" in out["skipped_ne"]
        in_total_ids = {d.id for d in D.DIMENSIONS if d.in_total}
        assert out["used_dimensions"] == len(in_total_ids - {"1"})

    def test_all_ne_returns_no_score(self):
        out = aggregate("PASS", False, self._scores(
            ne_dims={str(i) for i in range(1, 10)}))
        assert out["total_score"] is None
        assert out["verdict"] == "暂不可评"

    def test_low_coverage_flag(self):
        """有效权重低于阈值时应打 low_coverage 标记。"""
        from edu_eval.eval import dimensions as D

        keep = next(d.id for d in D.DIMENSIONS if d.in_total and d.weight > 0)
        s = self._scores(ne_dims={d.id for d in D.DIMENSIONS
                                  if d.in_total} - {keep})
        out = aggregate("PASS", False, s)
        assert out.get("low_coverage") is True


# ---------------------------------------------------------------- B 修复


class TestRulesInReport:
    """B：规则层结构化结果必须进入 Report（含 WARN 级证据）。"""

    def test_report_carries_rules_and_warnings(self, tmp_path):
        from edu_eval.config import Hy3Config
        from edu_eval.eval.run_eval import EvalContext, evaluate

        # 故意缺「作业」章节 → R-STRUCT warn 应出现在 rules 中
        doc = tmp_path / "lesson.md"
        doc.write_text(
            "# 有理数加减法（七年级）\n## 教学目标\n掌握有理数加减法。\n"
            "## 教学重点\n重点。\n## 教学过程\n过程。\n",
            encoding="utf-8")
        saved = os.environ.get("HY3_MOCK")
        os.environ["HY3_MOCK"] = "1"
        try:
            cfg = Hy3Config.from_env(require_key=False)
            rep = evaluate(str(doc), cfg,
                           EvalContext(grade="七年级", topic="有理数"))
        finally:
            if saved is None:
                os.environ.pop("HY3_MOCK", None)
            else:
                os.environ["HY3_MOCK"] = saved

        d = rep.to_dict()
        assert "rules" in d and d["rules"], "rules 未进入 Report"
        assert d["rules"].get("findings") is not None
        # 无告警：资产应全部装载成功
        assert d.get("warnings") == []
        # WARN 级证据（R-STRUCT）不得丢失
        warns = [f for f in d["rules"]["findings"]
                 if f["rule_id"] == "R-STRUCT" and f["verdict"] == "warn"]
        assert any("作业" in f["evidence"] for f in warns), \
            "R-STRUCT WARN 级证据在报告外壳中丢失（B 回归）"

    def test_missing_assets_produce_warning(self, monkeypatch, tmp_path):
        """资产缺失时必须在报告里显式告警（不再静默 PASS）。"""
        from edu_eval.config import Hy3Config
        from edu_eval.eval import run_eval
        from edu_eval.eval.run_eval import EvalContext

        monkeypatch.setenv("EDU_EVAL_DATA_DIR", str(tmp_path / "empty"))
        # 重新导入 paths 以读取新环境变量
        import importlib
        import edu_eval.paths
        importlib.reload(edu_eval.paths)

        doc = tmp_path / "lesson.md"
        doc.write_text("# 任意\n## 教学目标\nx\n## 教学重点\nx\n"
                       "## 教学过程\nx\n## 作业布置\nx", encoding="utf-8")
        saved = os.environ.get("HY3_MOCK")
        os.environ["HY3_MOCK"] = "1"
        try:
            cfg = Hy3Config.from_env(require_key=False)
            rep = run_eval.evaluate(str(doc), cfg, EvalContext(grade="七年级"))
        finally:
            if saved is None:
                os.environ.pop("HY3_MOCK", None)
            else:
                os.environ["HY3_MOCK"] = saved
            monkeypatch.delenv("EDU_EVAL_DATA_DIR", raising=False)
            importlib.reload(edu_eval.paths)

        assert rep.warnings, "资产缺失却无告警 —— 静默降级回归"
        assert any("不存在" in w or "未加载" in w or "未装载" in w
                   for w in rep.warnings)


# ---------------------------------------------------------------- G 修复


class TestPathIndependence:
    """G：资产路径不依赖当前工作目录。"""

    def test_paths_resolve_outside_repo(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)  # 工作目录切到仓库外
        assert os.path.exists(P.kb_jsonl())
        assert os.path.exists(P.grade_json())

    def test_kb_jsonl_absolute(self):
        assert os.path.isabs(P.kb_jsonl())

    def test_cli_from_foreign_cwd(self, tmp_path):
        """从仓库外目录跑 CLI，知识库必须装载（kb_hits > 0）。"""
        import subprocess

        doc = tmp_path / "lesson.md"
        doc.write_text(
            "# 一元一次方程（七年级）\n## 教学目标\n掌握一元一次方程。\n"
            "## 教学重点\n重点。\n## 教学过程\n过程。\n## 作业布置\n作业。",
            encoding="utf-8")
        env = {**os.environ, "HY3_MOCK": "1",
               "PYTHONPATH": str(ROOT / "src"),
               "EDU_EVAL_CACHE_DIR": str(tmp_path / "cache")}
        r = subprocess.run(
            [sys.executable, "-m", "edu_eval.cli", str(doc),
             "--grade", "七年级", "--mock", "--json"],
            cwd=str(tmp_path), env=env, capture_output=True, text=True,
            timeout=120)
        assert r.returncode == 0, r.stderr[-500:]
        d = json.loads(r.stdout)
        assert d["kb_hits"] > 0, "从仓库外运行时知识库未装载（G 回归）"
        assert d.get("warnings") == []
