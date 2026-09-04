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
