"""评测口径说明端点。

`GET /api/manual` 把「评测器到底怎么评」的结构化口径交给前端：
维度定义（名称 / 权重 / 优先级 / 描述 / 锚点 / 素养映射 / Judge 归属）、
Judge 分组、分档阈值、低覆盖比例、素养全集。

**单一事实来源**：全部实时读自 `eval/dimensions.py` 与 `eval/aggregator.py`，
前端不硬编码任何维度名、权重或分档阈值。改锚点或权重只需改 Python，
说明页自动同步——评测类产品最容易被质疑的就是"文档写的和跑的不一致"，
这里用「不抄常量」从结构上消掉这个漂移面。
"""

from __future__ import annotations

from fastapi import APIRouter

from ...eval import dimensions as D
from ...eval.aggregator import LOW_COVERAGE_RATIO, grade_label

router = APIRouter()

# 展示顺序：准入 → 底线 → 核心 → 教学质量 → 可用性 → 辅助
_PRI_ORDER = {"G0": 0, "P0": 1, "P1": 2, "P2": 3, "P3": 4, "AUX": 5}

# 键与 D.JUDGE_GROUPS 一致
_GROUP_LABELS = {
    "fact": "事实 Judge",
    "design": "教学设计 Judge",
    "expression_safety": "表达与安全 Judge",
}
_GROUP_DUTY = {
    "fact": "核验知识绝对正确性（G0 闸门）与学段适配",
    "design": "评价教学目标、环节设计、学情、教-学-评一致性与启发探究",
    "expression_safety": "评价表述清晰度、安全合规与格式可读性",
}


def _grade_bands() -> list[dict]:
    """分档阈值从 `grade_label` 反推，而不是把 85/70/60 再抄一遍。

    抄一遍就多一处会漂移的常量：哪天台了 aggregator 的阈值却忘了改这里，
    说明页就会给出与报告不一致的分档。这里直接探边界，阈值只有一个来源。
    """
    bands: list[dict] = []
    for s in range(0, 101):
        label = grade_label(float(s))
        if bands and bands[-1]["label"] == label:
            bands[-1]["max"] = s
        else:
            bands.append({"label": label, "min": s, "max": s})
    return bands


@router.get("/api/manual")
def manual() -> dict:
    dim_to_group = {d: g for g, ds in D.JUDGE_GROUPS.items() for d in ds}

    dims: list[dict] = []
    for d in D.DIMENSIONS:
        dims.append({
            "id": d.id,
            "name": d.name,
            "priority": d.priority,
            "weight": d.weight,
            "in_total": d.in_total,
            "auxiliary": d.auxiliary,
            "redline": d.redline,
            "description": d.description,
            "anchors": d.anchors,
            "competency_link": list(d.competency_link),
            "judge_group": dim_to_group.get(d.id, ""),
        })
    dims.sort(key=lambda x: (_PRI_ORDER.get(x["priority"], 9), x["id"]))

    return {
        "dimensions": dims,
        "weight_sum": D.weighted_total_weights(),
        "grade_bands": _grade_bands(),
        "low_coverage_ratio": LOW_COVERAGE_RATIO,
        "judge_groups": [
            {"key": k, "label": _GROUP_LABELS.get(k, k),
             "duty": _GROUP_DUTY.get(k, ""), "dims": list(v)}
            for k, v in D.JUDGE_GROUPS.items()
        ],
        "competencies": list(D.COMPETENCIES),
        "competency_aspect": dict(D.COMPETENCY_ASPECT),
    }
