"""额度耗尽熔断回归（真实事故：免费层日额度 50 被打满）。

事故：OpenRouter 免费层日额度（free-models-per-day，50 次/日）耗尽后，
后续**每一次**调用都返回 429。旧链路把 429 当「单个 Judge 失败」重试一次
后降级 NE，于是整份报告变成「所有维度 NE、看似正常实则完全无效」——最危险
的失败模式：一次无效全量跑浪费 128 次调用，还覆盖了有效基线。

修复：连续 429 达到阈值（3）判定为额度耗尽，抛 QuotaExceededError；
base 层不吞、向上传播，CLI 明确报错并退出。本文件覆盖正向 + 反方向 + 边界。

测试策略（项目约定）：每条规则补反方向与边界用例。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from edu_eval.config import Hy3Config
from edu_eval.hy3 import Hy3Client, QuotaExceededError
from edu_eval.eval.judges.fact import FactJudge


# ---------------------------------------------------------------- 假异常/客户端

class _Fake429(Exception):
    """模拟 OpenAI 库的 RateLimitError（status_code=429）。"""
    status_code = 429


class _Fake500(Exception):
    """模拟普通服务端错误（status_code=500，非限流）。"""
    status_code = 500


def _cfg() -> Hy3Config:
    return Hy3Config(base_url="https://example.invalid/v1",
                     api_key="mock", model="hy3", mock=True)


def _choice(content, finish_reason="stop"):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(finish_reason=finish_reason, message=msg)


def _resp(choices):
    return SimpleNamespace(choices=choices, model="hy3", usage=None)


def _client_with(sequence):
    """按序返回 sequence 中的项：异常则抛出，响应对象则返回。"""
    idx = {"i": 0}

    def _create(**kw):
        item = sequence[min(idx["i"], len(sequence) - 1)]
        idx["i"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    c = Hy3Client(_cfg())
    c._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=_create)))
    return c


# ---------------------------------------------------------------- 熔断正向

def test_three_consecutive_429_aborts():
    """连续 3 次 429 → 第 3 次抛 QuotaExceededError（熔断）。"""
    c = _client_with([_Fake429(), _Fake429(), _Fake429()])
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    with pytest.raises(QuotaExceededError):
        c.judge("s", "u")


def test_quota_error_message_names_consecutive_count():
    """熔断报错应包含连续计数，便于定位是抖动还是耗尽。"""
    c = _client_with([_Fake429()] * 3)
    for _ in range(2):
        with pytest.raises(_Fake429):
            c.judge("s", "u")
    with pytest.raises(QuotaExceededError, match="连续 3 次"):
        c.judge("s", "u")


# ---------------------------------------------------------------- 熔断反方向/边界

def test_two_consecutive_429_does_not_abort():
    """反方向：连续 2 次 429 未达阈值，只原样抛出，不熔断。"""
    c = _client_with([_Fake429(), _Fake429()])
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    assert c._consecutive_rate_limits == 2  # 计数保留但未触发熔断


def test_non_429_not_counted():
    """反方向：非 429 异常（如 500）不计入连续 429 计数。"""
    c = _client_with([_Fake500(), _Fake500(), _Fake500()])
    for _ in range(3):
        with pytest.raises(_Fake500):
            c.judge("s", "u")
    assert c._consecutive_rate_limits == 0


def test_success_resets_consecutive_counter():
    """边界：429 后一旦成功，连续计数清零，之后再来 429 重新从 1 计数。"""
    c = _client_with([
        _Fake429(), _Fake429(),
        _resp([_choice('{"a": 1}')]),  # 成功 → 清零
        _Fake429(), _Fake429(),
    ])
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    assert c.judge("s", "u") == '{"a": 1}'  # 成功清零
    with pytest.raises(_Fake429):
        c.judge("s", "u")
    with pytest.raises(_Fake429):
        c.judge("s", "u")  # 中间清零过，故仍未达阈值，只抛原异常


# ---------------------------------------------------------------- base 层不吞

class _QuotaClient:
    """始终抛 QuotaExceededError 的客户端，用于验证 base 层向上传播。"""
    cfg = SimpleNamespace(temperature=0.0, model="fake-model")

    def judge(self, system, user, *, temperature=None, max_tokens=None):
        raise QuotaExceededError("连续 3 次 429，额度耗尽")


def test_base_run_propagates_quota_error_instead_of_ne():
    """全局额度故障不得被降级成 NE：base.run() 必须向上传播。"""
    judge = FactJudge(_QuotaClient(), cache=None)
    with pytest.raises(QuotaExceededError):
        judge.run("教学设计正文。", {"grade": "九年级"})
