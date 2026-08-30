"""事实 Judge：知识准入（G0）+ 学段适配（维度 3）。

角色只负责「知识断言核验 + 学段匹配」；公式硬错误由规则层（rules.py）
前置判定，本 Judge 补充规则层无法覆盖的语义级知识检查。
"""
from __future__ import annotations

from typing import Any, Dict

from edu_eval.eval import dimensions as D
from edu_eval.eval.judges.base import BaseJudge


class FactJudge(BaseJudge):
    role = "fact"
    system = (
        "你是事实与学段裁判。你在知识库证据与程序计算基础上判断知识正确性（准入）和学段适配。"
        "必须逐项引用原文与知识库条目 ID。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "") -> str:
        lines = []
        for did in D.JUDGE_GROUPS["fact"]:
            dim = D.get_dimension(did)
            lines.append(
                f"### 维度 {did} {dim.name}（{dim.priority}，权重 {dim.weight}%）：\n"
                f"{dim.description}\n锚点：\n{dim.anchors}"
            )
        meta = self._meta(context)
        kb_block = f"\n\n【本地知识库检索结果】\n{kb_context}\n" if kb_context else ""
        return (
            f"{meta}\n\n【待评估教学设计正文】\n{text}\n\n"
            f"【需要评分的维度与量规】\n" + "\n\n".join(lines) + kb_block +
            "\n\n请返回严格 JSON：\n"
            "{\"admission\":\"PASS|FAIL|NE\",\"redline\":bool,"
            "\"scores\":{\"<维度id>\":{\"score\":1-5,\"evidence\":\"原文片段\",\"ne\":bool}},"
            "\"suggestions\":[\"改进建议\"]}"
        )

    @staticmethod
    def _meta(context: Dict[str, Any]) -> str:
        return (
            f"用户声明目标：年级={context.get('grade', '未声明')}；"
            f"教材版本={context.get('version', '未声明')}；"
            f"课题={context.get('topic', '未声明')}；"
            f"课时={context.get('period', '未声明')}"
        )
