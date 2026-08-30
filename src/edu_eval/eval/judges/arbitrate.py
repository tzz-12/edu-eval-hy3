"""仲裁 Judge：复核不通过的维度，重新独立评分并给出理由。

触发条件（由编排器决定）：
- 复核判证据无效的维度
- 需要跨 Judge 比对的高风险维度（如红线判定）
"""
from __future__ import annotations

from typing import Any, Dict

from edu_eval.eval import dimensions as D
from edu_eval.eval.judges.base import BaseJudge
from edu_eval.eval.judges.fact import FactJudge


class ArbitrateJudge(BaseJudge):
    role = "arbitrate"
    system = (
        "你是终审仲裁裁判。你只在主裁判与复核意见冲突时出场，必须独立重评争议维度，"
        "给出终审分数、终审证据与一句话理由。你的判断为最终结论。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "") -> str:
        # kb_context 此处复用为"争议维度 + 主裁判分数 + 复核意见"的 JSON
        lines = []
        for did in D.DIMENSIONS:
            if did.id in context.get("_disputed", []):
                lines.append(
                    f"### 争议维度 {did.id} {did.name}：\n"
                    f"{did.description}\n锚点：\n{did.anchors}"
                )
        meta = FactJudge._meta(context)
        return (
            f"{meta}\n\n【待评估教学设计正文】\n{text}\n\n"
            f"【争议维度量规】\n" + "\n\n".join(lines) +
            f"\n\n【主裁判与复核意见】\n{kb_context}\n\n"
            "请返回严格 JSON：\n"
            "{\"scores\":{\"<维度id>\":{\"score\":1-5,\"evidence\":\"原文片段\","
            "\"ne\":bool,\"reason\":\"终审理由\"}},\"suggestions\":[]}"
        )
