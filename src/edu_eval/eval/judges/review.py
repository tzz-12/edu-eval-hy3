"""复核 Judge：检查主 Judge 分数是否具备原文证据与量规依据。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from edu_eval.eval.judges.base import BaseJudge

# 证据中的"关键公式/符号跨度"：仅匹配 ASCII 数学/公式字符（不含中文，
# 因为 Python 3 的 \w 会匹配中文，会把整句中文误当成"公式跨度"再去要求
# 它作为连续子串出现）。用于判断证据里的数学式子是否在原文出现，而不是
# 要求整段证据是连续子串（ultra-550b 等模型常给"转述 + 引号公式"式证据）。
_MATH_SPAN = re.compile(r"[A-Za-z0-9+\-*/^=().,²³√π≈≠≤≥%]+")


class ReviewJudge(BaseJudge):
    role = "review"
    system = (
        "你是复核裁判。你逐条核对主裁判给出的分数与证据：证据中的关键公式/符号"
        "必须能在原文中找到（允许转述与标点差异），分数必须落在量规锚点区间内。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "",
                          rule_evidence: str = "") -> str:
        # 复核走结构化检查为主，LLM 为辅；此处提供统一校验说明
        return (
            f"【待核对原文】\n{text[:6000]}\n\n"
            f"【主裁判结果（JSON）】\n{kb_context}\n\n"
            "逐条检查 scores 中每个维度：evidence 里的关键公式/符号是否能在原文找到"
            "（允许转述与标点/空白差异）、score 是否在 1-5 且与证据方向一致。"
            "返回严格 JSON：\n"
            "{\"invalid\":[{\"dim\":\"维度id\",\"reason\":\"原因\"}],"
            "\"confirmed\":[\"维度id\"]}"
        )

    # ---- 确定性预检（不调用 LLM 即可完成大部分复核）----
    @staticmethod
    def deterministic_check(scores: Dict[str, Any], text: str) -> List[Dict[str, str]]:
        """规则复核：无证据 / 证据关键公式编造（原文不存在）的维度。

        判定口径（放宽，避免转述型证据被误判为「不匹配」而触发无谓仲裁）：
        - 无 evidence → 直接标问题；
        - 证据含数学跨度（公式/符号）→ 要求至少一个跨度能在原文找到，
          否则视为编造（真正的幻觉风险点）；
        - 纯文字证据 → 容忍转述，不触发仲裁（编造的纯文字风险较低，
          且会由「分数分歧 → 跨层仲裁」这条更可靠的路径兜底）。
        """
        problems: List[Dict[str, str]] = []
        norm_text = "".join(text.split())
        for did, s in scores.items():
            if not isinstance(s, dict) or s.get("ne"):
                continue
            ev = (s.get("evidence") or "").strip()
            if not ev:
                problems.append({"dim": str(did), "reason": "无证据支撑"})
                continue
            spans = _MATH_SPAN.findall(ev)
            if spans:
                # 关键公式必须能在原文找到（归一化后），否则才算编造
                if not any("".join(sp.split()) in norm_text for sp in spans):
                    problems.append({"dim": str(did),
                                     "reason": "证据中的公式/符号在原文中不存在（疑似编造）"})
        return problems

    def review(self, scores: Dict[str, Any], text: str) -> Dict[str, Any]:
        """复核入口：先确定性检查，再（可选）LLM 复核。"""
        problems = self.deterministic_check(scores, text)
        return {"problems": problems,
                "needs_arbitration": [p["dim"] for p in problems]}
