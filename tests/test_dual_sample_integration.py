"""双采样自一致接入正式架构的回归测试。

覆盖两条核心分支与两个易错点：
- |Δ| ≤ 阈值 → 取平均（四舍五入）；|Δ| > 阈值 或任一侧 ne → 层内仲裁；
- 两次视角自报 admission 不一致 → 保守降级 NE（非确定性强信号）；
- 编排层默认双采样（每个 Judge 两次调用），且可用 dual_sample=False 关闭；
- 两次采样必须走不同缓存键（视角指令进入 prompt，不得互相串味）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from edu_eval.config import Hy3Config
from edu_eval.eval import dimensions as D
from edu_eval.eval.dual_sample import (LENS_A, LENS_B, SampleArbitrator,
                                       dual_sample_judge, new_stats, summarize)
from edu_eval.eval.judges.design import DesignJudge
from edu_eval.eval.judges.expression_safety import ExpressionSafetyJudge
from edu_eval.eval.judges.fact import FactJudge
from edu_eval.eval.orchestrator import Orchestrator
from edu_eval.parse.parsers import ParsedDoc

DESIGN_DIMS = sorted(D.JUDGE_GROUPS["design"])

# 各角色 system 中的唯一标识（用于假客户端分派响应）
_ROLE_MARKS = {
    "事实与学段裁判": "fact",
    "教学设计裁判": "design",
    "表达与安全裁判": "expression_safety",
}


class _FakeLLM:
    """按 (role, lens) 返回预设 JSON 的假客户端，并完整记录调用。"""

    def __init__(self, responses, arb_score=3, arb_ne=False):
        self.responses = responses            # {(role, "a"|"b"): payload_dict}
        self.arb_score = arb_score
        self.arb_ne = arb_ne
        self.arb_calls = 0
        self.calls = []                       # [(role, lens, user)]
        self.cfg = SimpleNamespace(temperature=0.0, model="fake",
                                   timeout=30.0, max_tokens=8192)

    def judge(self, system: str, user: str, *, temperature=None,
              max_tokens=None) -> str:
        if "双采样分歧仲裁员" in system:
            self.arb_calls += 1
            return json.dumps(
                {"score": self.arb_score, "ne": self.arb_ne,
                 "evidence": "裁定依据", "rationale": "综合双方"},
                ensure_ascii=False)
        role = next((r for mark, r in _ROLE_MARKS.items() if mark in system), "?")
        lens = "a" if "严格按上方量规逐条逐项核对" in user else (
            "b" if "切换到「学习者" in user else "?")
        self.calls.append((role, lens, user))
        # 单采样模式不注入 _lens（lens="?"），等价于默认视角 → 回退到 "a"，
        # 否则假客户端抛错会被 base.run() 当成 API 异常静默重试，难以诊断。
        payload = self.responses.get((role, lens))
        if payload is None and lens == "?":
            payload = self.responses.get((role, "a"))
        if payload is None:
            raise AssertionError(f"未预设的响应：role={role} lens={lens}")
        return json.dumps(payload, ensure_ascii=False)


def _scores(dims, score):
    return {d: {"score": score, "ne": False, "evidence": "原文片段"}
            for d in dims}


def _design_responses(a_score, b_score, a_dim_override=None):
    """构造 design 角色两次采样的响应（可覆盖单个维度为 ne）。"""
    sa = _scores(DESIGN_DIMS, a_score)
    sb = _scores(DESIGN_DIMS, b_score)
    if a_dim_override:
        sa.update(a_dim_override)
    return {
        ("design", "a"): {"scores": sa, "suggestions": ["建议A"]},
        ("design", "b"): {"scores": sb, "suggestions": ["建议B"]},
    }


def _run_design(a_score, b_score, threshold=1, arb_score=3, **over):
    client = _FakeLLM(_design_responses(a_score, b_score, over.get("a_dim_override")),
                      arb_score=arb_score)
    judge = DesignJudge(client, cache=None)
    arb = SampleArbitrator(client, cache=None)
    stats = new_stats(threshold)
    out, stats = dual_sample_judge(judge, "教学设计正文。", {"grade": "九年级"},
                                   arb=arb, threshold=threshold, stats=stats)
    return client, out, stats


# ------------------------------------------------------- 分支一：分差 ≤ 阈值 → 平均

def test_small_diff_takes_average():
    """|Δ| ≤ 阈值 → 取平均（四舍五入），不惊动仲裁员。"""
    client, out, stats = _run_design(3, 4)
    for did in DESIGN_DIMS:
        rec = out["scores"][did]
        assert rec["score"] == 4, "(3+4)/2=3.5 应四舍五入为 4"
        assert rec["resolution"] == "average"
        assert (rec["a"], rec["b"], rec["diff"]) == (3, 4, 1)
    assert client.arb_calls == 0
    assert stats["arbitrations"] == 0


def test_identical_samples_need_no_arbitration():
    """两次完全一致（真实跑中占 70%）→ 全部走平均路径。"""
    client, out, stats = _run_design(4, 4)
    assert all(r["diff"] == 0 for r in out["scores"].values())
    assert client.arb_calls == 0
    assert summarize(stats)["diff_zero"] == len(DESIGN_DIMS)


# ------------------------------------------------- 分支二：分差 > 阈值 → 层内仲裁

def test_large_diff_triggers_arbitration():
    """|Δ| > 阈值 → 交层内仲裁员，最终分取裁定分而非平均。"""
    client, out, stats = _run_design(1, 5, arb_score=3)
    for did in DESIGN_DIMS:
        rec = out["scores"][did]
        assert rec["score"] == 3, "应采信仲裁裁定分，而非 (1+5)/2=3 的巧合值"
        assert rec["resolution"] == "arbitrated"
        assert rec["rationale"] == "综合双方"
    assert client.arb_calls == len(DESIGN_DIMS)
    assert stats["arbitrations"] == len(DESIGN_DIMS)


def test_arbitration_decides_distinct_from_average():
    """反方向护栏：裁定分必须能区别于平均值（1 与 5 平均=3，裁定给 2）。"""
    _, out, _ = _run_design(1, 5, arb_score=2)
    assert out["scores"][DESIGN_DIMS[0]]["score"] == 2


def test_boundary_diff_equal_threshold_takes_average():
    """边界：|Δ| 恰好等于阈值（1）→ 走平均，不仲裁。"""
    client, out, _ = _run_design(2, 3, threshold=1)
    assert out["scores"][DESIGN_DIMS[0]]["resolution"] == "average"
    assert client.arb_calls == 0


def test_boundary_diff_one_over_threshold_arbitrates():
    """边界：|Δ| = 阈值+1（2）→ 仲裁。"""
    client, out, _ = _run_design(2, 4, threshold=1)
    assert out["scores"][DESIGN_DIMS[0]]["resolution"] == "arbitrated"
    assert client.arb_calls == len(DESIGN_DIMS)


# ------------------------------------------------- 分支三：一侧 ne / 缺失 → 仲裁

def test_one_side_ne_triggers_arbitration():
    """任一次采样标记 ne → 不确定性不一致，同样交仲裁（resolution 可区分）。"""
    client, out, stats = _run_design(
        4, 4, arb_score=4, a_dim_override={DESIGN_DIMS[0]: {"ne": True,
                                                            "reason": "无法核验"}})
    rec = out["scores"][DESIGN_DIMS[0]]
    assert rec["resolution"] == "arbitrated_ne", "需与「分差过大」的仲裁区分开"
    assert rec["score"] == 4
    # 其余维度不受影响，仍走平均
    assert out["scores"][DESIGN_DIMS[1]]["resolution"] == "average"


def test_arbitrator_may_return_ne():
    """仲裁员判 ne → score 必须为 None，不得留下旧分造成「有分却不可判定」。"""
    client = _FakeLLM(_design_responses(1, 5), arb_score=3, arb_ne=True)
    judge = DesignJudge(client, cache=None)
    out, _ = dual_sample_judge(judge, "正文", {}, arb=SampleArbitrator(client, None))
    rec = out["scores"][DESIGN_DIMS[0]]
    assert rec["ne"] is True
    assert rec["score"] is None or rec["ne"] is True


# ------------------------------------------------- admission 合并：不一致 → 保守 NE

def _fact_responses(adm_a, adm_b):
    return {
        ("fact", "a"): {"admission": adm_a, "redline": False,
                        "scores": _scores(["2", "3"], 4), "suggestions": []},
        ("fact", "b"): {"admission": adm_b, "redline": False,
                        "scores": _scores(["2", "3"], 4), "suggestions": []},
    }


def test_admission_inconsistent_degrades_to_ne():
    """两次视角连「是否准入」都不一致 → NE（真实跑中正方形/平面镶嵌即此情形）。"""
    client = _FakeLLM(_fact_responses("PASS", "FAIL"))
    judge = FactJudge(client, cache=None)
    out, _ = dual_sample_judge(judge, "正文", {}, arb=SampleArbitrator(client, None))
    assert out["admission"] == "NE"
    assert out["_dual_sample"]["admission_consistent"] is False


def test_admission_consistent_preserved():
    client = _FakeLLM(_fact_responses("PASS", "PASS"))
    judge = FactJudge(client, cache=None)
    out, _ = dual_sample_judge(judge, "正文", {}, arb=SampleArbitrator(client, None))
    assert out["admission"] == "PASS"
    assert out["_dual_sample"]["admission_consistent"] is True


def test_redline_from_either_sample_preserved():
    """红线是安全信号：任一采样命中即保留，不能被另一次未命中抹掉。"""
    resp = _fact_responses("PASS", "PASS")
    resp[("fact", "b")]["redline"] = True
    client = _FakeLLM(resp)
    judge = FactJudge(client, cache=None)
    out, _ = dual_sample_judge(judge, "正文", {}, arb=SampleArbitrator(client, None))
    assert out["redline"] is True


# ------------------------------------------------- 缓存不串味：两次采样提示必须不同

def test_two_lenses_produce_distinct_prompts():
    """视角指令进入 prompt → cache_key 自动换键，两次采样互不串味。"""
    client = _FakeLLM(_design_responses(3, 4))
    judge = DesignJudge(client, cache=None)
    dual_sample_judge(judge, "正文", {}, arb=SampleArbitrator(client, None))
    users = [u for _, _, u in client.calls]
    assert len(users) == 2
    assert users[0] != users[1], "两次采样提示相同会导致缓存串味、双采样退化成单采样"
    assert "严格按上方量规逐条逐项核对" in users[0]
    assert "切换到「学习者" in users[1]


# ------------------------------------------------- 编排层集成：默认开 / 可关闭

def _all_responses(score=4):
    """三个角色 × 两个视角的统一响应（避免任何分歧，专注验证调用次数）。"""
    resp = {}
    for role, dims in (("fact", ["2", "3"]),
                       ("design", sorted(D.JUDGE_GROUPS["design"])),
                       ("expression_safety", sorted(D.JUDGE_GROUPS["expression_safety"]))):
        for lens in ("a", "b"):
            payload = {"scores": _scores(dims, score), "suggestions": []}
            if role == "fact":
                payload["admission"] = "PASS"
            if role == "expression_safety":
                payload["redline"] = False
            resp[(role, lens)] = payload
    return resp


class _NoDisputeReview:
    def review(self, scores, text):
        return {"problems": [], "needs_arbitration": []}


def _orch_with(client, dual_sample=True):
    cfg = Hy3Config(base_url="https://example.invalid/v1", api_key="mock",
                    model="hy3", mock=True)
    orch = Orchestrator(cfg, use_cache=False, dual_sample=dual_sample)
    orch.client = client
    orch.j_fact = FactJudge(client, None)
    orch.j_design = DesignJudge(client, None)
    orch.j_expr = ExpressionSafetyJudge(client, None)
    orch.j_arb_sample = SampleArbitrator(client, None)
    orch.j_review = _NoDisputeReview()      # 复核无争议，隔离出双采样本身的行为
    orch.j_arbit = type("A", (), {"run": lambda self, *a, **k: {"scores": {}}})()
    return orch


def _parsed():
    return ParsedDoc(text="二次函数 y=a(x-h)²+k 的图象与性质教学设计。").finalize()


def test_orchestrator_dual_sample_by_default_calls_each_judge_twice():
    """默认双采样：三个 Judge 各调两次（共 6 次），且无分歧时不惊动仲裁员。"""
    client = _FakeLLM(_all_responses())
    report = _orch_with(client).run(_parsed(), {"grade": "九年级"})
    assert len(client.calls) == 6, f"期望 3 Judge × 2 视角，实际 {len(client.calls)}"
    assert client.arb_calls == 0
    ds = report["dual_sample"]
    assert ds["dims_total"] == 10, "维度 2/3 + 1/4/7/8/9 + 5/6/A 共 10 个"
    assert ds["arbitrated"] == 0
    assert ds["threshold"] == 1


def test_orchestrator_single_sample_when_disabled():
    """反方向：dual_sample=False → 每个 Judge 只调一次（回退旧行为）。"""
    client = _FakeLLM(_all_responses())
    report = _orch_with(client, dual_sample=False).run(_parsed(), {"grade": "九年级"})
    assert len(client.calls) == 3
    assert client.arb_calls == 0
    assert report["dual_sample"]["dims_total"] == 0, "未走双采样，统计应为空"


def test_orchestrator_dual_sample_stats_present_on_early_return():
    """边界：G0 判 FAIL 提前返回时，报告也要带 dual_sample 统计（不能缺字段）。"""
    resp = _all_responses()
    for lens in ("a", "b"):
        resp[("fact", lens)] = {**resp[("fact", lens)],
                                "scores": {"2": {"score": 2, "ne": False,
                                                 "evidence": "知识错误"},
                                           "3": {"score": 4, "ne": False,
                                                 "evidence": "x"}}}
    client = _FakeLLM(resp)
    report = _orch_with(client).run(_parsed(), {"grade": "九年级"})
    assert report["admission"] == "FAIL"
    assert "dual_sample" in report
    assert report["dual_sample"]["dims_total"] == 2, "短路只跑了 fact 层的 2 个维度"


def test_orchestrator_arbitration_counted_in_stats():
    """存在真实分歧时，仲裁次数要如实计入统计（供一致性实验分析）。"""
    resp = _all_responses(score=4)
    resp[("design", "b")] = {
        "scores": _scores(sorted(D.JUDGE_GROUPS["design"]), 1),
        "suggestions": []}
    client = _FakeLLM(resp, arb_score=3)
    report = _orch_with(client).run(_parsed(), {"grade": "九年级"})
    ds = report["dual_sample"]
    assert ds["arbitrated"] == len(D.JUDGE_GROUPS["design"])
    assert client.arb_calls == len(D.JUDGE_GROUPS["design"])
    assert ds["diff_max"] == 3
