"""分数类型归一化回归（ultra-550b 双采样真实跑暴露的 bug）。

事故：模型偶发把 score 写成字符串 "3"。原链路中 _schema_valid 只认 int，
既白重试一次，重试后仍把字符串原样返回 → 下游（聚合 / 双采样 _num）把 "3"
当缺失，本该 average 的维度错走 arbitrated_ne。

本文件覆盖正向转换 + 反方向（非数值不得被"救活"）+ 边界（ne / 浮点 / 布尔）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from edu_eval.eval.judges.base import BaseJudge
from edu_eval.eval.judges.fact import FactJudge


class _FakeClient:
    """返回固定 JSON 的假客户端，并统计真实调用次数（用于验证不再白重试）。"""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0
        self.cfg = SimpleNamespace(temperature=0.0, model="fake-model")

    def judge(self, system: str, user: str, *, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False)


def _fact_payload(scores: dict) -> dict:
    return {"admission": "PASS", "redline": False, "scores": scores,
            "suggestions": []}


def _run_fact(scores: dict):
    client = _FakeClient(_fact_payload(scores))
    judge = FactJudge(client, cache=None)
    data = judge.run("教学设计正文。", {"grade": "九年级"})
    return client, judge.normalize(data)


def test_string_score_coerced_to_int_and_no_wasted_retry():
    """模型返回 "3" → 归一化为 int 3，且不应因结构非法白重试第二次。"""
    client, data = _run_fact({"2": {"score": "3", "evidence": "原文片段"},
                              "3": {"score": 4, "evidence": "原文片段"}})
    assert data["scores"]["2"]["score"] == 3
    assert isinstance(data["scores"]["2"]["score"], int)
    assert client.calls == 1, "字符串分数应在解析层修正，而不是靠重试"


def test_numeric_string_with_decimal_coerced():
    client, data = _run_fact({"2": {"score": "4.0", "evidence": "e"},
                              "3": {"score": 4, "evidence": "e"}})
    assert data["scores"]["2"]["score"] == 4
    assert client.calls == 1


def test_float_score_rounded_to_int():
    """浮点 3.7 → 4（四舍五入），保证下游拿到的始终是 int。"""
    client, data = _run_fact({"2": {"score": 3.7, "evidence": "e"},
                              "3": {"score": 4, "evidence": "e"}})
    assert data["scores"]["2"]["score"] == 4
    assert client.calls == 1


def test_non_numeric_string_stays_invalid():
    """反方向：非数值字符串不得被救活，必须仍判结构非法（触发重试）。"""
    client, _ = _run_fact({"2": {"score": "优秀", "evidence": "e"},
                           "3": {"score": 4, "evidence": "e"}})
    assert client.calls == 2, "非数值分数应判非法并重试一次"


def test_ne_dimension_untouched():
    """边界：ne 维度没有 score，不得被凭空补出分数字段。"""
    _, data = _run_fact({"2": {"ne": True, "reason": "原文未涉及"},
                         "3": {"score": 4, "evidence": "e"}})
    assert data["scores"]["2"].get("ne") is True
    assert "score" not in data["scores"]["2"]


def test_boolean_score_not_treated_as_int():
    """边界：bool 是 int 子类，但 True 不是合法分数，不得被转成 1。"""
    coerced = BaseJudge._coerce_score(True)
    assert coerced is True


def test_top_level_score_coerced_for_sample_arbitrator():
    """采样仲裁员输出的是顶层 score（非 scores 结构），同样要归一化。"""
    data = BaseJudge._coerce_scores({"score": "2", "ne": False})
    assert data["score"] == 2
    assert isinstance(data["score"], int)


def test_normalize_coerces_without_run():
    """normalize 是正式链路收口：即便 data 不经过 run()，也要保证 int。"""
    data = BaseJudge.normalize({"scores": {"2": {"score": "5", "evidence": "e"}}})
    assert data["scores"]["2"]["score"] == 5
