"""表达与安全 Judge：维度 5 / 6（红线）/ A（辅助）。"""
from __future__ import annotations

from typing import Any, Dict

from edu_eval.eval import dimensions as D
from edu_eval.eval.judges.base import BaseJudge
from edu_eval.eval.judges.fact import FactJudge


class ExpressionSafetyJudge(BaseJudge):
    role = "expression_safety"
    system = (
        "你是表达与安全裁判。你评估表述清晰度、安全合规与价值导向（含红线）以及格式可读性。"
        "红线判断必须给出原文证据与风险类别。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "",
                          rule_evidence: str = "") -> str:
        lines = []
        for did in D.JUDGE_GROUPS["expression_safety"]:
            dim = D.get_dimension(did)
            lines.append(
                f"### 维度 {did} {dim.name}（{dim.priority}，权重 {dim.weight}%"
                f"{'，辅助维度不计入总分' if dim.auxiliary else ''}）：\n"
                f"{dim.description}\n锚点：\n{dim.anchors}"
                + self._competency_block(context, did)
            )
        meta = FactJudge._meta(context)
        lens = self._lens_directive(context)
        return (
            f"{meta}\n\n{lens}"
            f"【待评估教学设计正文】\n{text}\n\n"
            f"【需要评分的维度与量规】\n" + "\n\n".join(lines) +
            "\n\n请返回严格 JSON（注意：scores 的键必须是上方列出的维度 id 本身"
            "（如 \"5\"、\"6\"、\"A\"），禁止使用 \"维度 5\" 等前缀形式"
            "或 G0/P0 等优先级标签）：\n"
            "{\"redline\":bool,"
            "\"scores\":{\"<维度id>\":{\"score\":1-5,\"evidence\":\"原文片段\",\"ne\":bool}},"
            "\"suggestions\":[\"改进建议\"]}"
        )
