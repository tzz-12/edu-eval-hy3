"""Hy3Client 客户端层回归测试：空 choices / 截断重试 / 空内容。

背景（真实事故）：OpenRouter 免费层偶发返回 200 但 choices=None（上游
provider 瞬时过载/限流）。旧逻辑 resp.choices[0] 直接抛 TypeError，被
base 层吞成误导性的「JSON 解析失败」。修复后改为显式抛「空 choices」的
可重试 RuntimeError，让 base 层按「API 异常」路径重试而非「坏输出」路径。

测试策略（遵循项目约定）：每条规则补反方向与边界用例。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from edu_eval.config import Hy3Config
from edu_eval.hy3 import Hy3Client


def _cfg() -> Hy3Config:
    return Hy3Config(base_url="https://example.invalid/v1",
                     api_key="mock", model="hy3", mock=True)


def _client(resp) -> Hy3Client:
    """构造一个 mock=True 的客户端，再把 _client 换成返回固定 resp 的假对象。"""
    c = Hy3Client(_cfg())  # mock=True → _client 保持 None，不真正 import/连 openai
    create = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=lambda **kw: resp)))
    c._client = create
    return c


def _choice(content, finish_reason="stop"):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(finish_reason=finish_reason, message=msg)


def _resp(choices, model="hy3"):
    return SimpleNamespace(choices=choices, model=model, usage=None)


# ---------------------------------------------------------------- 空 choices

def test_choices_none_raises_clear_error():
    """choices=None（200 但无候选）→ 显式抛「空 choices」，而非 TypeError。"""
    c = _client(_resp(choices=None))
    with pytest.raises(RuntimeError, match="空 choices"):
        c.judge("sys", "user")


def test_choices_empty_list_raises_clear_error():
    """choices=[]（空列表）同为空响应，也应抛「空 choices」。"""
    c = _client(_resp(choices=[]))
    with pytest.raises(RuntimeError, match="空 choices"):
        c.judge("sys", "user")


# ---------------------------------------------------------------- 正常路径

def test_normal_choice_returns_content():
    """正常响应返回 content，不被误杀。"""
    c = _client(_resp([_choice('{"a": 1}')]))
    assert c.judge("sys", "user") == '{"a": 1}'


def test_none_content_treated_as_empty():
    """message.content 为 None → 等价空内容，走空内容报错路径。"""
    c = _client(_resp([_choice(None)]))
    with pytest.raises(RuntimeError, match="空内容"):
        c.judge("sys", "user")


# ---------------------------------------------------------------- 截断重试

def test_length_finish_reason_retries_with_doubled_budget():
    """finish_reason=length 时用翻倍预算重试；第二次完整即返回重试结果。"""
    calls = []

    def _create(**kw):
        calls.append(kw["max_tokens"])
        if len(calls) == 1:
            # 第一次：截断（思维链吃满预算）
            return _resp([_choice('{"a":', finish_reason="length")])
        return _resp([_choice('{"a": 1}')])

    c = Hy3Client(_cfg())
    c._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=_create)))
    assert c.judge("sys", "user") == '{"a": 1}'
    assert calls == [8192, 16384]  # 8192 → 翻倍 16384（未超 65536 上限）


def test_length_retry_capped_at_65536():
    """翻倍预算封顶 65536，避免无限膨胀。"""
    calls = []

    def _create(**kw):
        calls.append(kw["max_tokens"])
        if len(calls) == 1:
            return _resp([_choice("x", finish_reason="length")])
        return _resp([_choice('{"a": 1}')])

    c = Hy3Client(_cfg())
    # 直接传一个很大的 max_tokens 参数，模拟 budget 已接近上限
    c._client = SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=_create)))
    c.judge("sys", "user", max_tokens=50000)
    assert calls == [50000, 65536]  # 50000*2=100000 → 封顶 65536


# ---------------------------------------------------------------- 429 退避重试
#
# 背景（2026-09-11）：TokenHub 上 preview 档模型（hy4-preview）容量很小，
# 实测首次请求约 60~75% 返回 429（code 429006「模型服务繁忙或已达服务容量上限」）。
# OpenAI SDK 自带的 2 次重试覆盖不了：429 会一路抛到 base 层，而 base 层对
# API 异常只重试一次就把该维度降级成 NE —— 一份报告里大半维度变 NE，看起来
# 「跑完了」实际无效。故在客户端层消化瞬时 429（指数退避）。
#
# 与熔断的边界：只有**重试全部失败**的那一次才计入 _consecutive_rate_limits，
# 所以高容量压力不会被误判成「额度耗尽」。

class _Fake429(Exception):
    """模拟 OpenAI 的 RateLimitError（status_code=429）。"""
    status_code = 429


class _Fake500(Exception):
    """模拟普通服务端错误（非限流，不应被重试逻辑吞掉）。"""
    status_code = 500


def _seq_client(sequence, monkeypatch, retries=3, backoff=0):
    """按序投放「异常 / 响应」的假客户端；退避 sleep 被打桩记录。"""
    import edu_eval.hy3 as hy3mod
    monkeypatch.setattr(hy3mod, "RATE_LIMIT_RETRIES", retries)
    monkeypatch.setattr(hy3mod, "RATE_LIMIT_BACKOFF_CAP", backoff)
    slept: list = []
    monkeypatch.setattr(hy3mod.time, "sleep", lambda s: slept.append(s))

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
    return c, slept, idx


def test_429_then_success_retries_and_returns(monkeypatch):
    """正向：首次 429、第二次成功 → 返回内容，且不算作「额度耗尽」。"""
    c, slept, idx = _seq_client(
        [_Fake429(), _resp([_choice('{"a": 1}')])], monkeypatch, backoff=10)
    assert c.judge("sys", "user") == '{"a": 1}'
    assert idx["i"] == 2                    # 恰好重试一次
    assert slept == [1]                     # 退避 2^0 = 1s
    assert c._consecutive_rate_limits == 0  # 成功即清零


def test_exhausted_retries_raise_and_count_once(monkeypatch):
    """边界：重试全部失败 → 抛原异常，且熔断计数只 +1（不是每次重试都 +1）。"""
    c, slept, idx = _seq_client([_Fake429()], monkeypatch, retries=3, backoff=10)
    with pytest.raises(_Fake429):
        c.judge("sys", "user")
    assert idx["i"] == 3                    # 用满 3 次尝试
    assert slept == [1, 2]                  # 最后一次失败后退避无意义，不再等待
    assert c._consecutive_rate_limits == 1


def test_backoff_is_capped(monkeypatch):
    """边界：退避按 2^n 增长但受 RATE_LIMIT_BACKOFF_CAP 封顶，避免等待失控。"""
    c, slept, _ = _seq_client([_Fake429()], monkeypatch, retries=5, backoff=4)
    with pytest.raises(_Fake429):
        c.judge("sys", "user")
    assert slept == [1, 2, 4, 4]            # 2^3=8 被 cap=4 压回 4


def test_non_429_is_not_retried(monkeypatch):
    """反方向：非 429（如 500）不进入退避重试，只调用一次即抛。"""
    c, slept, idx = _seq_client([_Fake500()], monkeypatch)
    with pytest.raises(_Fake500):
        c.judge("sys", "user")
    assert idx["i"] == 1
    assert slept == []
    assert c._consecutive_rate_limits == 0  # 非 429 不计入熔断计数


def test_retries_disabled_keeps_legacy_semantics(monkeypatch):
    """反方向：RATE_LIMIT_RETRIES=1 时退化为改动前行为（一次调用一次请求）。"""
    c, slept, idx = _seq_client([_Fake429()], monkeypatch, retries=1)
    with pytest.raises(_Fake429):
        c.judge("sys", "user")
    assert idx["i"] == 1
    assert slept == []
    assert c._consecutive_rate_limits == 1


def test_429_across_calls_still_trips_circuit_breaker(monkeypatch):
    """边界：单次调用内重试耗尽后，跨调用连续 3 次仍会熔断（真实额度耗尽场景）。"""
    from edu_eval.hy3 import QuotaExceededError
    c, _, _ = _seq_client([_Fake429()], monkeypatch, retries=2)
    with pytest.raises(_Fake429):
        c.judge("sys", "user")
    with pytest.raises(_Fake429):
        c.judge("sys", "user")
    with pytest.raises(QuotaExceededError):
        c.judge("sys", "user")

