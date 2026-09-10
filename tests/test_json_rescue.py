"""坏 JSON 抢救（_rescue_scores / _parse_json）的单元测试。

背景
----
真实模型（deepseek-v4-flash-0731 实测）偶发在 evidence 中照抄原文，带出
**未转义的英文直引号**，把 JSON 字符串提前闭合，导致整份输出无法解析。
实测发生率 25~40%，既非截断（finish_reason=stop）也非网络错误。

系统提示里加「禁止英文直引号」铁律后失败率未下降，因此改为在解析层兜底：
按结构锚点 `"<dim>": {"score": N, "evidence": "...", "ne": ...}` 抢救。

测试覆盖
--------
- 正常 JSON 不得触发抢救（不能好心办坏事、把合法输出改坏）
- 坏 JSON 能救回全部维度的分数与证据
- 无关文本不得凭空造出分数（假阳性比漏抢救危险得多）
- 部分维度可救时如实返回，由 _output_valid 决定是否重试
- evidence 内部含换行、中文引号等边界
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from edu_eval.eval.judges.base import BaseJudge  # noqa: E402

DIMS = ["1", "4", "7", "8", "9"]


def _good_json() -> str:
    body = ",".join(
        f'"{d}": {{"score": {i + 1}, "evidence": "证据{d}", "ne": false}}'
        for i, d in enumerate(DIMS))
    return '{"scores":{' + body + '},"suggestions":["建议"]}'


def _bad_json() -> str:
    """evidence 中夹带未转义英文直引号——真实事故现场的最小复现。"""
    return '''{
  "scores": {
    "1": {"score": 5, "evidence": "目标1『能结合"买文具找零"等情境』", "ne": false},
    "4": {"score": 2, "evidence": "环节只写教师动作", "ne": false},
    "7": {"score": 4, "evidence": "小测诊断"判断 3+4=7"是否成立", "ne": false},
    "8": {"score": 3, "evidence": "评价止于登记", "ne": false},
    "9": {"score": 2, "evidence": "封闭问"是不是"，齐声答", "ne": false}
  },
  "suggestions": ["补充反馈路径"]
}'''


def test_good_json_not_repaired():
    """合法 JSON 必须原样解析，不得触发抢救路径。"""
    data = BaseJudge._parse_json(_good_json())
    assert not data.get("_repaired")
    assert not data.get("_parse_failed")
    assert set(data["scores"]) == set(DIMS)
    assert data["scores"]["8"]["score"] == 4


def test_bad_json_rescues_all_dims():
    """含未转义引号的坏 JSON：五个维度的分数与证据都要救回来。"""
    data = BaseJudge._parse_json(_bad_json())
    assert data.get("_repaired") is True, "应标记为抢救结果以便留痕"
    assert not data.get("_parse_failed")
    scores = data["scores"]
    assert set(scores) == set(DIMS)
    assert [scores[d]["score"] for d in DIMS] == [5, 2, 4, 3, 2]
    # 证据要救回来：判别力实验靠它算「证据定位率」
    assert "买文具找零" in scores["1"]["evidence"]
    assert "齐声答" in scores["9"]["evidence"]


def test_irrelevant_text_yields_no_scores():
    """反方向：无关文本不得凭空造出分数。

    抢救失败最多是丢一次调用；凭空造分会污染评测结论却无人察觉，
    所以这条比上一条更重要。
    """
    data = BaseJudge._parse_json("抱歉，我无法完成该请求。")
    assert data.get("_parse_failed") is True
    assert data["scores"] == {}


def test_json_without_scores_key_not_invented():
    """反方向：合法 JSON 但缺 scores，抢救逻辑不得凭空补一个出来。

    注意这里走的是第一级（json.loads 成功）直接原样返回，压根不会到抢救——
    这是正确行为：解析层只管「能不能读出来」，「读出来的够不够」由
    _output_valid 判定并触发重试。解析层擅自补 scores 反而会掩盖坏输出。
    """
    data = BaseJudge._parse_json('{"suggestions": ["a", "b"]}')
    assert not data.get("_repaired")
    assert "scores" not in data, "不得凭空补 scores"


def test_partial_rescue_reported_as_is():
    """只救回部分维度时如实返回，是否重试交给 _output_valid 判断。

    抢救层不应替上层决定「够不够」——它的职责只是尽量少丢信息。
    """
    partial = ('{"scores": {"1": {"score": 5, "evidence": "含"引号"", "ne": false}, '
               '"4": {"score": 3, "evidence": "正常", "ne": false}}}')
    data = BaseJudge._parse_json(partial)
    assert data.get("_repaired") is True
    assert set(data["scores"]) == {"1", "4"}


def test_evidence_with_newlines_and_cjk_quotes():
    """边界：evidence 含换行与中文引号时，锚点仍应正确收敛。"""
    raw = ('{"scores": {"8": {"score": 3, "evidence": "第一行\\n'
           '第二行『引用』", "ne": false}, "9": {"score": 2, '
           '"evidence": "结尾", "ne": false}}}')
    data = BaseJudge._parse_json(raw)
    # 这份本身是合法 JSON，走正常路径
    assert not data.get("_repaired")
    assert data["scores"]["8"]["evidence"].startswith("第一行")


def test_rescue_keeps_first_occurrence():
    """同一维度重复出现时以首次为准，避免被后文（如 suggestions）覆盖。"""
    dup = ('{"scores": {"1": {"score": 5, "evidence": "含"引号"", "ne": false}}, '
           '"other": {"1": {"score": 1, "evidence": "误导", "ne": false}}}')
    data = BaseJudge._parse_json(dup)
    assert data["scores"]["1"]["score"] == 5
