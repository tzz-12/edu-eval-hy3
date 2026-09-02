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
                          kb_context: str = "", rule_evidence: str = "") -> str:
        lines = []
        for did in D.JUDGE_GROUPS["fact"]:
            dim = D.get_dimension(did)
            lines.append(
                f"### 维度 {did} {dim.name}（{dim.priority}，权重 {dim.weight}%）：\n"
                f"{dim.description}\n锚点：\n{dim.anchors}"
            )
        meta = self._meta(context)
        kb_block = f"\n\n【本地知识库检索结果】\n{kb_context}\n" if kb_context else ""
        rule_block = (
            "\n\n【规则层确定性判定（程序计算，非模型推断，优先级最高）】\n"
            f"{rule_evidence}\n"
            "以上判定由符号计算与查表得出，除非你能指出其计算或依据有误，"
            "否则必须采纳：\n"
            "- 判定为超纲的概念 → 维度 3（学段适配）不得高于 2 分，"
            "并在 evidence 中引用该概念；\n"
            "- 判定为豁免/联想的概念 → 不构成超纲依据，不得据此扣分。\n"
        ) if rule_evidence else ""
        return (
            f"{meta}\n\n【待评估教学设计正文】\n{text}\n\n"
            f"【需要评分的维度与量规】\n" + "\n\n".join(lines)
            + rule_block + kb_block +
            "\n\n请返回严格 JSON（注意：scores 的键必须是上方列出的维度 id 本身"
            "（如 \"2\"、\"3\"），禁止使用 G0/P1 等优先级标签作为键）：\n"
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

    def _output_valid(self, data: Dict[str, Any]) -> bool:
        """G0 角色校验：自报 PASS 却没给维度 2 判定 → 无效（实测偶发）。

        admission=PASS 意味着模型声称已核验知识正确性，此时维度 2
        （知识准确，G0 的评分载体）必须给出 score/ne 判定，否则
        准入结论没有任何判定依据，只能整体 NE（见 orchestrator.resolve_admission）。
        重试一次通常能拿到带维度 2 的完整输出。
        """
        if data.get("_parse_failed"):
            return False
        if str(data.get("admission", "")).upper() == "PASS":
            g0 = (data.get("scores") or {}).get("2")
            if not isinstance(g0, dict):
                return False
        return True
