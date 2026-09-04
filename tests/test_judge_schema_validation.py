"""Judge 输出「结构校验」回归测试。

背景（真实事故）：hy3 偶发把「多维度评判」退化成「单维度结构」——
顶层给出 score/evidence/ne，同时多出一个**非 dict 的 scores 字段**（整数 1）。

修复前：基类 _output_valid 恒返回 True → 畸形输出被判有效 →
        不重试、且**写进缓存造成污染**；下游 normalize 才发现 scores 非 dict
        并清空，最终表现为「该 Judge 所有维度分数为 null」，被误读成「模型拒答」。

修复后：结构校验前置（_schema_valid），畸形输出判无效 → 重试 → 仍失败则不写缓存。

测试策略（遵循本项目约定）：每条规则都补**反方向与边界**用例，
尤其要覆盖「合法但不规范」的输出不能被误杀（否则会白白重试、浪费额度）。
"""
from __future__ import annotations

import json

import pytest

from edu_eval.config import Hy3Config
from edu_eval.eval.cache import JudgeCache
from edu_eval.eval.judges.base import BaseJudge
from edu_eval.eval.judges.design import DesignJudge
from edu_eval.eval.judges.expression_safety import ExpressionSafetyJudge
from edu_eval.eval.judges.fact import FactJudge


class _ScriptedClient:
    """按序返回预设响应的假客户端（用尽后重复返回最后一条）。"""

    def __init__(self, *responses: str):
        self.cfg = Hy3Config(base_url="https://example.invalid/v1",
                             api_key="mock", model="hy3", mock=True)
        self._responses = list(responses)
        self.calls = 0

    def judge(self, system, user, *, temperature=None, max_tokens=None) -> str:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]


def _ok_payload(dims, score=3, **over):
    body = {d: {"score": score, "ne": False, "evidence": "原文片段"}
            for d in dims}
    body.update(over)
    return json.dumps({"scores": body, "suggestions": []}, ensure_ascii=False)


# 真实事故样本：退化成单维度结构，scores 被写成整数 1
_DEGENERATED = json.dumps({
    "score": 3, "ne": False,
    "evidence": "教学目标（1）掌握二次函数 y = a(x-h)²+k 的图象和性质…",
    "scores": 1,
}, ensure_ascii=False)

DESIGN_DIMS = ("1", "4", "7", "8", "9")


def _design(client) -> DesignJudge:
    return DesignJudge(client)


# ---------------------------------------------------------------- 正向：合法输出

def test_valid_full_output_accepted():
    j = _design(_ScriptedClient(_ok_payload(DESIGN_DIMS)))
    data = j.run("正文", {})
    assert j._output_valid(data) is True
    assert set(data["scores"]) == set(DESIGN_DIMS)


def test_ne_dimension_accepted_without_score():
    """ne=True 是合法判定，不要求 score 落在 1–5。"""
    payload = json.dumps({
        "scores": {"1": {"score": 3, "ne": False, "evidence": "x"},
                   "4": {"ne": True, "evidence": "无法核验"},
                   "7": {"score": 3, "ne": False, "evidence": "x"},
                   "8": {"score": 3, "ne": False, "evidence": "x"},
                   "9": {"score": 3, "ne": False, "evidence": "x"}},
    }, ensure_ascii=False)
    j = _design(_ScriptedClient(payload))
    assert j._output_valid(j.run("正文", {})) is True


def test_cjk_style_keys_not_falsely_rejected():
    """键写成「维度 1」应被归一化后接受——不能把「不规范但正确」误杀（浪费重试额度）。"""
    payload = json.dumps({
        "scores": {f"维度 {d}": {"score": 3, "ne": False, "evidence": "x"}
                   for d in DESIGN_DIMS},
    }, ensure_ascii=False)
    j = _design(_ScriptedClient(payload))
    data = j.run("正文", {})
    # 校验阶段内部已归一化，因此不会被误杀（不会白白重试）
    assert j._output_valid(data) is True
    # run() 不负责改键（那是 normalize 的职责），显式验证归一化后键正确归位
    assert set(BaseJudge.normalize(dict(data))["scores"]) == set(DESIGN_DIMS)


def test_boundary_scores_1_and_5_accepted():
    """边界值 1 与 5 必须接受（1–5 闭区间）。"""
    for s in (1, 5):
        j = _design(_ScriptedClient(_ok_payload(DESIGN_DIMS, score=s)))
        assert j._output_valid(j.run("正文", {})) is True


# ---------------------------------------------------------- 反向：畸形输出必须拦下

def test_degenerated_scores_int_rejected():
    """事故样本本体：scores 为整数 + 顶层单维度结构 → 必须判无效。"""
    j = _design(_ScriptedClient(_DEGENERATED))
    assert j._output_valid(j.run("正文", {})) is False


@pytest.mark.parametrize("bad", [1, "x", [], None, 3.5])
def test_scores_not_dict_rejected(bad):
    payload = json.dumps({"score": 3, "ne": False, "evidence": "x",
                          "scores": bad}, ensure_ascii=False)
    j = _design(_ScriptedClient(payload))
    assert j._output_valid(j.run("正文", {})) is False


def test_scores_missing_rejected():
    j = _design(_ScriptedClient(json.dumps({"admission": "PASS"})))
    assert j._output_valid(j.run("正文", {})) is False


def test_missing_dimension_rejected():
    """少评一个维度 → 无效（宁可重试，也不能让下游静默缺失）。"""
    j = _design(_ScriptedClient(_ok_payload(("1", "4", "7", "8"))))  # 缺 9
    assert j._output_valid(j.run("正文", {})) is False


@pytest.mark.parametrize("bad_score", [0, 6, -1, "3", 3.0, None, True])
def test_out_of_range_or_wrong_type_score_rejected(bad_score):
    body = {d: {"score": 3, "ne": False, "evidence": "x"} for d in DESIGN_DIMS}
    body["7"] = {"score": bad_score, "ne": False, "evidence": "x"}
    j = _design(_ScriptedClient(json.dumps({"scores": body})))
    assert j._output_valid(j.run("正文", {})) is False


def test_dimension_value_not_dict_rejected():
    body = {d: {"score": 3, "ne": False, "evidence": "x"} for d in DESIGN_DIMS}
    body["4"] = 3  # 直接给数字而非判定对象
    j = _design(_ScriptedClient(json.dumps({"scores": body})))
    assert j._output_valid(j.run("正文", {})) is False


# ------------------------------------------------ 关键不变式：畸形结果绝不写缓存

def test_degenerated_result_never_cached(tmp_path):
    """畸形输出不得落盘缓存——否则同键复用会永久产出 null（缓存污染）。"""
    cache = JudgeCache(enabled=True, cache_dir=str(tmp_path))
    j = DesignJudge(_ScriptedClient(_DEGENERATED), cache)
    j.run("正文", {})
    assert list(tmp_path.glob("*.json")) == [], "畸形结果被写进了缓存，会造成污染"


def test_valid_result_is_cached(tmp_path):
    """对照组：合法输出应当正常落盘（证明上面那条不是因为缓存整体失效）。"""
    cache = JudgeCache(enabled=True, cache_dir=str(tmp_path))
    j = DesignJudge(_ScriptedClient(_ok_payload(DESIGN_DIMS)), cache)
    j.run("正文", {})
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_retry_recovers_from_degenerated():
    """首次畸形、重试拿到合法输出 → 最终采用合法结果（重试机制真的生效）。"""
    client = _ScriptedClient(_DEGENERATED, _ok_payload(DESIGN_DIMS))
    j = _design(client)
    data = j.run("正文", {})
    assert client.calls == 2, "畸形输出应触发一次重试"
    assert set(data["scores"]) == set(DESIGN_DIMS)


# --------------------------------------------------------------- 覆盖另外两个 Judge

def test_expression_safety_schema_enforced():
    j = ExpressionSafetyJudge(_ScriptedClient(_DEGENERATED))
    assert j._output_valid(j.run("正文", {})) is False
    j2 = ExpressionSafetyJudge(_ScriptedClient(_ok_payload(("5", "6", "A"))))
    assert j2._output_valid(j2.run("正文", {})) is True


def test_fact_scores_int_does_not_crash():
    """回归：修复前 (1).get("2") 会抛 AttributeError 直接崩掉评测。"""
    payload = json.dumps({"admission": "PASS", "redline": False,
                          "scores": 1}, ensure_ascii=False)
    j = FactJudge(_ScriptedClient(payload))
    data = j.run("正文", {})  # 不得抛异常
    assert j._output_valid(data) is False


def test_fact_missing_dim2_when_pass_still_rejected():
    """FactJudge 原有专属规则（自报 PASS 必须有维度 2）不能被新校验覆盖掉。"""
    payload = json.dumps({
        "admission": "PASS", "redline": False,
        "scores": {"3": {"score": 5, "ne": False, "evidence": "x"}},
    }, ensure_ascii=False)
    j = FactJudge(_ScriptedClient(payload))
    assert j._output_valid(j.run("正文", {})) is False


# ------------------------------------------------------------------ 豁免与作用域

def test_role_outside_judge_groups_exempt():
    """不在 JUDGE_GROUPS 里的角色（如层内采样仲裁员）不受多维结构校验约束。"""
    class _Loose(BaseJudge):
        role = "sample_arbitrator"

    j = _Loose(_ScriptedClient("{}"))
    assert j._output_valid({"score": 4, "ne": False, "evidence": "x"}) is True


def test_normalize_shares_key_rule_with_schema_check():
    """normalize 与 _schema_valid 必须共用同一套键归一化规则，避免规则漂移。"""
    scores = {"维度 1": {"score": 3, "ne": False}, "4": {"score": 3, "ne": False}}
    fixed = BaseJudge._normalize_score_keys(scores, {"1", "4"})
    assert set(fixed) == {"1", "4"}


# ------------------------------------------------------------ 事故场景端到端复现

def test_degenerated_first_then_retry_recovers_all_dimensions():
    """端到端复现真实事故：strict_rubric 采样首次返回畸形 → 重试 → 维度全部拿回。

    修复前：畸形被判有效、直接落缓存 → normalize 把 scores 清空 →
            design 的 5 个维度全为 None，被误读成「模型拒答」，
            并连带触发 5 次本不必要的仲裁（污染一致性实验统计）。
    """
    class _LensClient:
        """按「审视视角」分派响应：strict_rubric 首次给畸形，重试给正常。"""

        def __init__(self):
            self.cfg = Hy3Config(base_url="https://example.invalid/v1",
                                 api_key="mock", model="hy3", mock=True)
            self.strict_calls = 0
            self.calls = 0

        def judge(self, system, user, *, temperature=None, max_tokens=None):
            self.calls += 1
            if "严格按上方量规逐条逐项核对" in user:  # strict_rubric 视角特征
                self.strict_calls += 1
                return (_DEGENERATED if self.strict_calls == 1
                        else _ok_payload(DESIGN_DIMS))
            return _ok_payload(DESIGN_DIMS)  # learner_view 始终正常

    client = _LensClient()
    judge = DesignJudge(client)
    ra = BaseJudge.normalize(judge.run("正文", {"_lens": "strict_rubric"}))
    rb = BaseJudge.normalize(judge.run("正文", {"_lens": "learner_view"}))
    sa, sb = ra["scores"], rb["scores"]

    assert client.strict_calls == 2, "畸形输出应触发一次重试"
    assert set(sa) == set(DESIGN_DIMS), "strict_rubric 采样维度仍缺失"
    assert set(sb) == set(DESIGN_DIMS)
    # 关键不变式：不再退化为「全 None」，每个维度都拿到可比较的数值分
    for did in DESIGN_DIMS:
        assert isinstance(sa[did].get("score"), int)
        assert isinstance(sb[did].get("score"), int)
