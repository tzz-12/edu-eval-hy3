"""评分标尺（rubric）锚点的回归保护。

背景：真实 API 跑通后发现维度 9「学习者中心与启发探究」判别力不足——
注入教科书级伪启发段落（"是不是这样？对不对？…无需学生独立思考"）后，
维度 9 只从 4 降到 3，总分 74→72 仅差 2 分。

根因：锚点只在「有无提问」层面分档，1 分要求"没有学生思考任务"，
3 分是"存在有意义的问题或任务"，伪启发正好落进 3 分的灰色地带——
文档里确实有提问，只是学生不需要真实思考。修复方式是补 2 分档 +
显式列出伪启发反模式与降级规则。

本文件锁住这些约束，防止锚点被改回去而没人发现。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from edu_eval.eval.cache import PROMPT_VERSION  # noqa: E402
from edu_eval.eval.dimensions import DIMENSIONS  # noqa: E402


def _dim(did: str):
    for d in DIMENSIONS:
        if d.id == did:
            return d
    raise AssertionError(f"维度 {did} 不存在")


# 维度 9 description + anchors 的基线哈希。
# 若此测试失败，说明维度 9 的评分标尺变了 —— 这是**允许**的，但必须：
#   1) 确认改动是有意为之；
#   2) 同步升 edu_eval.eval.cache.PROMPT_VERSION（否则旧 Judge 缓存不失效，
#      线上会继续用旧标尺的结果，表现为"改了不生效"）；
#   3) 更新下面的哈希与 EXPECTED_PROMPT_VERSION。
EXPECTED_PROMPT_VERSION = "v5"
DIM9_BASELINE_SHA = "e2d9afe596c3d8347dbc3c522eb2c742f7e6d07b12e44abbbf5d3c182a15597e"


def test_dim9_anchors_pinned_to_prompt_version():
    """锚点内容变更必须伴随 PROMPT_VERSION 升版，否则旧缓存不会失效。"""
    d = _dim("9")
    blob = (d.description + d.anchors).encode("utf-8")
    sha = hashlib.sha256(blob).hexdigest()
    if sha != DIM9_BASELINE_SHA:
        assert PROMPT_VERSION != EXPECTED_PROMPT_VERSION, (
            "维度 9 的锚点已改动，但 PROMPT_VERSION 仍是 "
            f"{EXPECTED_PROMPT_VERSION}。标尺变了却没升版，旧 Judge 缓存不会失效，"
            "线上会继续返回按旧标尺算出的分数。请升 PROMPT_VERSION，"
            f"并把本文件的 DIM9_BASELINE_SHA 更新为 {sha}、"
            "EXPECTED_PROMPT_VERSION 更新为新版本号。"
        )


def test_dim9_has_pseudo_inquiry_anti_pattern():
    """伪启发的四个可观测特征必须显式写在锚点里。

    少任何一条，Judge 都只能凭印象判断"这算不算启发"，
    而模型默认倾向是"有问号就算探究"。
    """
    anchors = _dim("9").anchors
    # ① 封闭是非问 + 齐声应答
    assert "是不是" in anchors and "对不对" in anchors and "齐声应答" in anchors
    # ② 提问后立即公布答案 / 直接给结论
    assert "标准答案" in anchors and "不追问理由" in anchors
    # ③ 学生活动只是核对抄写
    assert "核对" in anchors and "抄写" in anchors
    # ④ 文本明确声明无需思考
    assert "无需独立思考" in anchors


def test_dim9_has_downgrade_rule_capped_at_2():
    """命中伪启发必须降级且封顶 2 分。

    这是修复的关键：文档其他部分可能确实有真实探究（描点画图、小组合作），
    若不写死"即使别处有真实探究，主导环节是伪启发也不得高于 2 分"，
    Judge 会取"整体还行"的折中印象分，判别力就被稀释掉了。
    """
    anchors = _dim("9").anchors
    assert "降级规则" in anchors
    assert "不得高于 2 分" in anchors


def test_dim9_has_low_anchor_band():
    """必须有 2 分档。

    原先只有 1/3/5 三档，1 分要求"完全没有"学生任务，
    伪启发够不到 1 分、又该低于 3 分，无档可落 → 被 3 分接住。
    """
    assert "2 分：" in _dim("9").anchors


def test_dim9_requires_quoting_evidence_on_downgrade():
    """降级必须引用命中的原文，便于人工审计。"""
    assert "引用命中的具体原文" in _dim("9").anchors


def test_all_dimensions_have_anchor_bands():
    """结构完整性：每个维度都要有 1/3/5 分档且锚点非空。

    反方向用例——防止有人新增维度时只写 description 忘写 anchors，
    那样 Judge 拿到的是无标尺提示，分数全凭模型心情。
    """
    for d in DIMENSIONS:
        assert d.anchors and d.anchors.strip(), f"维度 {d.id} 锚点为空"
        assert "1 分" in d.anchors, f"维度 {d.id} 缺 1 分锚点"
        assert "3 分" in d.anchors, f"维度 {d.id} 缺 3 分锚点"
        assert "5 分" in d.anchors, f"维度 {d.id} 缺 5 分锚点"
