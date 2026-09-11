"""报告必须记录裁判身份（模型 / 端点 / 采样参数）—— 提交材料的证据链。

背景（2026-09-11）：课题要求「全程通过 API 调用 Hy3」，但此前 3 份演示报告的
JSON 里搜不到任何模型信息（`model` / `base_url` 出现 0 次），评审无从核实这份
报告由哪个模型产出，换模型前后的产物也无法区分——证据链是断的。
故在 Report 增加 judge 字段，并保证：① 不泄漏密钥；② 端点只留 host。

测试策略（项目约定）：每条规则补反方向与边界用例。
"""
from __future__ import annotations

from edu_eval.config import Hy3Config
from edu_eval.eval.run_eval import Report, _build_judge_meta


def _cfg(**kw) -> Hy3Config:
    base = dict(base_url="https://tokenhub.tencentmaas.com/v1",
                api_key="sk-secret-should-never-appear",
                model="hy4-preview", mock=False, max_tokens=16384)
    base.update(kw)
    return Hy3Config(**base)


# ---------------------------------------------------------------- 正向

def test_judge_meta_records_model_host_and_params():
    m = _build_judge_meta(_cfg(), elapsed_s=12.34, dual_sample=True, dual_threshold=1)
    assert m["model"] == "hy4-preview"
    assert m["endpoint_host"] == "tokenhub.tencentmaas.com"
    assert m["dual_sample"] is True
    assert m["dual_threshold"] == 1
    assert m["max_tokens"] == 16384
    assert m["mock"] is False
    assert m["elapsed_s"] == 12.3
    assert "T" in m["generated_at"]  # ISO 8601


def test_judge_meta_marks_mock_mode():
    m = _build_judge_meta(_cfg(mock=True), elapsed_s=0.0,
                          dual_sample=False, dual_threshold=1)
    assert m["mock"] is True
    assert m["dual_sample"] is False


# ---------------------------------------------------------------- 反方向 / 边界

def test_judge_meta_never_leaks_api_key():
    """密钥绝不能进报告——报告是要随仓库公开的。"""
    m = _build_judge_meta(_cfg(), elapsed_s=0, dual_sample=True, dual_threshold=1)
    assert "sk-secret-should-never-appear" not in str(m)
    assert all("key" not in k.lower() for k in m)


def test_judge_meta_keeps_host_only_not_full_url():
    """端点只留 host，不带路径/查询串（后者可能含凭据）。"""
    m = _build_judge_meta(
        _cfg(base_url="https://example.com/v1/chat?token=abc"),
        elapsed_s=0, dual_sample=True, dual_threshold=1)
    assert m["endpoint_host"] == "example.com"
    assert "token=abc" not in str(m)


def test_judge_meta_survives_malformed_url():
    """边界：畸形 URL 不该让整份评测崩掉，回退为原字符串。"""
    m = _build_judge_meta(_cfg(base_url="not a url"), elapsed_s=0,
                          dual_sample=True, dual_threshold=1)
    assert m["endpoint_host"] == "not a url"


def test_report_to_dict_has_judge_key_by_default():
    """反方向：未填充时也必须是空 dict（而非缺键），前端取字段才不报错。"""
    assert Report().to_dict()["judge"] == {}


def test_report_to_dict_exposes_judge():
    r = Report(admission="PASS")
    r.judge = {"model": "hy3"}
    assert r.to_dict()["judge"] == {"model": "hy3"}


# ---------------------------------------------------------------- 文本报告

def test_to_text_shows_judge_model_and_host():
    r = Report(admission="PASS")
    r.judge = {"model": "hy4-preview", "endpoint_host": "tokenhub.tencentmaas.com"}
    text = r.to_text()
    assert "hy4-preview" in text
    assert "tokenhub.tencentmaas.com" in text


def test_to_text_without_judge_meta_does_not_crash():
    """反方向：老报告（无 judge 字段）仍能正常出文本。"""
    text = Report(admission="PASS").to_text()
    assert "知识准入：PASS" in text
    assert "裁判模型" not in text
