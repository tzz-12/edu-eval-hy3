"""确定性聚合：知识准入优先，红线兜底，加权总分。"""
from __future__ import annotations

from typing import Any, Dict

from . import dimensions as D


def grade_label(total: float) -> str:
    if total >= 85:
        return "优秀"
    if total >= 70:
        return "良好"
    if total >= 60:
        return "合格"
    return "待改进"


def aggregate(admission: str, redline: bool, scores: Dict[str, Any]) -> Dict[str, Any]:
    """聚合各维度分数。

    - admission == FAIL → 总评“不通过”（不生成教学质量总分）
    - admission == NE   → 总评“暂不可评”（不生成总分）
    - redline == True   → 总评“不通过”
    - 否则按 8 个加权维度计算 Σ(维度得分/5 × 权重)
    """
    if admission == "FAIL":
        return {"verdict": "不通过", "total_score": None, "grade": None,
                "reason": "知识正确性准入为 FAIL：存在确认的知识错误。"}
    if admission == "NE":
        return {"verdict": "暂不可评", "total_score": None, "grade": None,
                "reason": "知识正确性准入为 NE：核心断言无法核验（知识库未覆盖/来源冲突/解析不可靠）。"}
    if redline:
        return {"verdict": "不通过", "total_score": None, "grade": None,
                "reason": "安全红线触发：存在严重不适龄/违法违规/歧视等内容。"}

    weighted = 0.0
    used = 0
    skipped_ne = []
    for dim in D.DIMENSIONS:
        if not dim.in_total:
            continue
        s = scores.get(dim.id)
        if not isinstance(s, dict):
            skipped_ne.append(dim.id)
            continue
        if s.get("ne"):
            skipped_ne.append(dim.id)
            continue
        score = float(s.get("score", 0))
        weighted += (score / 5.0) * dim.weight
        used += 1

    total = round(weighted, 2)
    return {
        "verdict": "通过" if total >= 60 else "待改进",
        "total_score": total,
        "grade": grade_label(total),
        "used_dimensions": used,
        "skipped_ne": skipped_ne,
        "reason": "",
    }
