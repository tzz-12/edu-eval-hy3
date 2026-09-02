"""确定性聚合：知识准入优先，红线兜底，加权总分。"""
from __future__ import annotations

from typing import Any, Dict

from . import dimensions as D

#: 有效权重低于总权重的该比例时，总分虽给出但标记为低覆盖（结论需谨慎引用）
LOW_COVERAGE_RATIO = 0.6


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
    - 否则按 8 个加权维度计算 Σ(维度得分/5 × 权重) / Σ(有效权重) × 100

    P0-10 · F 修复：NE（无法判定）维度**按有效权重归一化**，不再当 0 分。
    旧实现把 NE 维度的权重原样留在分母里、分子却不加分，等价于给该维度打 0 分：
    实测「八个维度全 5 分」得 100，把权重 20 的维度 1 改成 NE 后掉到 80 ——
    这等于把**知识库缺口/证据不足**算成了**样本的质量问题**，会系统性压低
    那些恰好缺少可核验依据的样本，直接污染 Phase 1 的判别力实验。
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

    total_weight = D.weighted_total_weights()
    weighted = 0.0
    covered_weight = 0.0
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
        covered_weight += dim.weight
        used += 1

    if covered_weight <= 0:
        return {
            "verdict": "暂不可评", "total_score": None, "grade": None,
            "used_dimensions": 0, "skipped_ne": skipped_ne,
            "covered_weight": 0.0,
            "reason": "所有计分维度均为 NE：证据不足，无法给出教学质量总分。",
        }

    total = round(weighted / covered_weight * 100.0, 2)
    out = {
        "verdict": "通过" if total >= 60 else "待改进",
        "total_score": total,
        "grade": grade_label(total),
        "used_dimensions": used,
        "skipped_ne": skipped_ne,
        "covered_weight": round(covered_weight, 2),
        "weight_coverage": round(covered_weight / total_weight, 3),
        "reason": "",
    }
    if covered_weight / total_weight < LOW_COVERAGE_RATIO:
        out["low_coverage"] = True
        out["reason"] = (
            f"仅 {used} 个维度可判定（覆盖权重 {covered_weight:.0f}% / "
            f"{total_weight:.0f}%），总分仅供参考。"
        )
    return out
