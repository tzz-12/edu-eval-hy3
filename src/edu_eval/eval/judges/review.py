"""复核 Judge：检查主 Judge 分数是否具备原文证据与量规依据。"""
from __future__ import annotations

import difflib
from typing import Any, Dict, List

from edu_eval.eval.judges.base import BaseJudge


class ReviewJudge(BaseJudge):
    role = "review"
    system = (
        "你是复核裁判。你逐条核对主裁判给出的分数与证据：证据必须是原文的连续片段，"
        "分数必须落在量规锚点区间内。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "") -> str:
        # 复核走结构化检查为主，LLM 为辅；此处提供统一校验说明
        return (
            f"【待核对原文】\n{text[:6000]}\n\n"
            f"【主裁判结果（JSON）】\n{kb_context}\n\n"
            "逐条检查 scores 中每个维度：evidence 是否为原文连续片段（允许标点/空白差异）、"
            "score 是否在 1-5 且与证据方向一致。返回严格 JSON：\n"
            "{\"invalid\":[{\"dim\":\"维度id\",\"reason\":\"原因\"}],"
            "\"confirmed\":[\"维度id\"]}"
        )

    # ---- 确定性预检（不调用 LLM 即可完成大部分复核）----
    @staticmethod
    def deterministic_check(scores: Dict[str, Any], text: str) -> List[Dict[str, str]]:
        """规则复核：无证据 / 证据不在原文（相似度过低）的维度。"""
        problems: List[Dict[str, str]] = []
        norm_text = "".join(text.split())
        for did, s in scores.items():
            if not isinstance(s, dict) or s.get("ne"):
                continue
            ev = (s.get("evidence") or "").strip()
            if not ev:
                problems.append({"dim": str(did), "reason": "无证据支撑"})
                continue
            norm_ev = "".join(ev.split())
            if norm_ev not in norm_text:
                ratio = difflib.SequenceMatcher(
                    None, norm_ev[:80], norm_text).find_longest_match(
                    0, len(norm_ev[:80]), 0, len(norm_text))
                if ratio.size < max(6, len(norm_ev[:80]) * 0.5):
                    problems.append({"dim": str(did),
                                     "reason": "证据片段与原文不匹配"})
        return problems

    def review(self, scores: Dict[str, Any], text: str) -> Dict[str, Any]:
        """复核入口：先确定性检查，再（可选）LLM 复核。"""
        problems = self.deterministic_check(scores, text)
        return {"problems": problems,
                "needs_arbitration": [p["dim"] for p in problems]}
