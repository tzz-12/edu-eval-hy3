"""教学设计 Judge：维度 1 / 4 / 7 / 8 / 9。"""
from __future__ import annotations

from typing import Any, Dict

from edu_eval.eval import dimensions as D
from edu_eval.eval.judges.base import BaseJudge
from edu_eval.eval.judges.fact import FactJudge


class DesignJudge(BaseJudge):
    role = "design"
    system = (
        "你是教学设计裁判。你依据量规评估教学目标、环节、学情、教—学—评一致性与学习者中心。"
        "必须引用原文片段作为证据。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "",
                          rule_evidence: str = "") -> str:
        lines = []
        for did in D.JUDGE_GROUPS["design"]:
            dim = D.get_dimension(did)
            lines.append(
                f"### 维度 {did} {dim.name}（{dim.priority}，权重 {dim.weight}%）：\n"
                f"{dim.description}\n锚点：\n{dim.anchors}"
                + self._competency_block(context, did)
            )
        meta = FactJudge._meta(context)
        return (
            f"{meta}\n\n【待评估教学设计正文】\n{text}\n\n"
            f"【需要评分的维度与量规】\n" + "\n\n".join(lines) +
            "\n\n素养导向判定提示：原文若只出现“培养核心素养”等口号而无具体内容对应，"
            "按各维度锚点的强制规则不予加分，并在 evidence 中说明缺什么。"
            "\n\n请返回严格 JSON（注意：scores 的键必须是上方列出的维度 id 本身"
            "（如 \"1\"、\"4\"、\"7\"、\"8\"、\"9\"），禁止使用 \"维度 1\" 等前缀形式"
            "或 G0/P1 等优先级标签）：\n"
            "{\"scores\":{\"<维度id>\":{\"score\":1-5,\"evidence\":\"原文片段\",\"ne\":bool}},"
            "\"suggestions\":[\"改进建议\"]}"
        )
