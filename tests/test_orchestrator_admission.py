"""编排器「仲裁后准入重决议」回归测试。

背景（真实事故）：FactJudge 把维度 2（G0）判 4 分（PASS）→ resolve_admission 判 PASS →
复核/仲裁把维度 2 覆写成 1 分（FAIL）→ 但聚合阶段硬编码 admission="PASS"，
最终出现「维度 2 = 1 分却仍按 PASS 给总分」的自相矛盾报告。

修复：聚合前对最终 scores 重新决议准入。

测试策略（遵循项目约定）：正反两方向——仲裁把维度 2 覆写成低分（→FAIL）、
高分（→PASS）、以及仲裁不碰维度 2（→保持 PASS）三种分支。
"""
from __future__ import annotations

from edu_eval.config import Hy3Config
from edu_eval.eval.orchestrator import Orchestrator, resolve_admission
from edu_eval.parse.parsers import ParsedDoc


def _orch() -> Orchestrator:
    cfg = Hy3Config(base_url="https://example.invalid/v1", api_key="mock",
                    model="hy3", mock=True)
    return Orchestrator(cfg)  # 空 KB + mock 客户端，纯内存，零网络


class _FakeFact:
    """维度 2 首次判 4 分（PASS），触发 line 183 的 resolve_admission → PASS。"""
    def run(self, text, context, kb_context="", rule_evidence=""):
        return {"admission": "PASS", "redline": False,
                "scores": {"2": {"score": 4, "ne": False, "evidence": "x"},
                           "3": {"score": 5, "ne": False, "evidence": "x"}},
                "suggestions": []}
    def normalize(self, d):
        return d


class _FakeDesign:
    def run(self, text, context):
        return {"scores": {d: {"score": 4, "ne": False, "evidence": "x"}
                           for d in ("1", "4", "7", "8", "9")}, "suggestions": []}
    def normalize(self, d):
        return d


class _FakeExpr:
    def run(self, text, context):
        return {"redline": False,
                "scores": {d: {"score": 4, "ne": False, "evidence": "x"}
                           for d in ("5", "6", "A")}, "suggestions": []}
    def normalize(self, d):
        return d


class _FakeReview:
    def __init__(self, disputed):
        self._disputed = disputed
    def review(self, scores, text):
        return {"problems": [{"dim": d, "reason": "证据不匹配"} for d in self._disputed],
                "needs_arbitration": self._disputed}


class _FakeArbit:
    """仲裁把维度 2 覆写为指定分数。"""
    def __init__(self, dim2_score):
        self._dim2_score = dim2_score
    def run(self, text, context, kb_context=""):
        return {"scores": {"2": {"score": self._dim2_score, "ne": False,
                                 "evidence": "仲裁覆写", "arbitrated": True}}}


def _run(dim2_after_arbit, disputed=("2",)):
    orch = _orch()
    orch.j_fact = _FakeFact()
    orch.j_design = _FakeDesign()
    orch.j_expr = _FakeExpr()
    orch.j_review = _FakeReview(list(disputed))
    orch.j_arbit = _FakeArbit(dim2_after_arbit)
    parsed = ParsedDoc(text="二次函数 y = a(x-h)+k 的图象和性质。").finalize()
    return orch.run(parsed, {"grade": "九年级", "version": "人教版",
                             "topic": "二次函数", "period": "1课时"})


def test_arbitration_overrides_dim2_to_fail_re_resolves_admission():
    """仲裁把维度 2 从 4 覆写成 1 → 准入须重决议为 FAIL，不给总分。"""
    report = _run(dim2_after_arbit=1)
    assert report["admission"] == "FAIL"
    assert report["aggregation"]["total_score"] is None
    assert report["aggregation"]["verdict"] == "不通过"


def test_arbitration_overrides_dim2_to_high_keeps_pass():
    """仲裁把维度 2 覆写成 5 → 准入保持 PASS，正常给总分。"""
    report = _run(dim2_after_arbit=5)
    assert report["admission"] == "PASS"
    assert report["aggregation"]["total_score"] is not None


def test_no_arbitration_on_dim2_keeps_pass():
    """仲裁不碰维度 2 → 准入保持 PASS（原行为不变）。"""
    report = _run(dim2_after_arbit=4, disputed=("3",))  # 只仲裁维度 3
    assert report["admission"] == "PASS"


# ---------------------------------------------------------------- 纯函数边界

def test_resolve_admission_dim2_missing_returns_ne():
    assert resolve_admission("PASS", {}) == "NE"


def test_resolve_admission_dim2_score1_returns_fail():
    assert resolve_admission("PASS", {"2": {"score": 1, "ne": False}}) == "FAIL"


def test_resolve_admission_dim2_score4_returns_declared():
    assert resolve_admission("PASS", {"2": {"score": 4, "ne": False}}) == "PASS"
