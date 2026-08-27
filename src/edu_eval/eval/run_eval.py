"""评估主流程：解析 → 知识准入 → 多 Judge 评分 → 复核/仲裁 → 聚合 → 报告。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Hy3Config
from ..hy3 import Hy3Client
from ..parse.parsers import ParsedDoc, parse_file
from . import dimensions as D
from .aggregator import aggregate
from .judge import multimodal_cross_check, run_judge_group
from .knowledge_base import KnowledgeBase


@dataclass
class EvalContext:
    grade: str = ""
    version: str = ""
    topic: str = ""
    period: str = ""


@dataclass
class Report:
    admission: str = ""
    redline: bool = False
    scores: Dict[str, Any] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    aggregation: Dict[str, Any] = field(default_factory=dict)
    parse: Dict[str, Any] = field(default_factory=dict)
    arbitration: List[str] = field(default_factory=list)
    kb_hits: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "admission": self.admission,
            "redline": self.redline,
            "scores": self.scores,
            "suggestions": self.suggestions,
            "aggregation": self.aggregation,
            "parse": self.parse,
            "arbitration": self.arbitration,
            "kb_hits": self.kb_hits,
        }

    def to_text(self) -> str:
        lines = []
        lines.append("=" * 48)
        lines.append("EduEval 评估报告（基于混元 Hy3 · 个人/活动作品）")
        lines.append("=" * 48)
        lines.append(f"知识准入：{self.admission}    安全红线：{'是' if self.redline else '否'}")
        if self.parse:
            lines.append(f"解析状态：{self.parse.get('parse_status')}（置信度 {self.parse.get('parse_confidence')}）")
        lines.append("-" * 48)
        for dim in D.DIMENSIONS:
            s = self.scores.get(dim.id)
            if isinstance(s, dict):
                tag = "NE" if s.get("ne") else f"{s.get('score')}/5"
                lines.append(f"[{dim.priority}] 维度 {dim.id} {dim.name}（{dim.weight}%）：{tag}")
                ev = (s.get("evidence") or "").strip().replace("\n", " ")
                if ev:
                    lines.append(f"    证据：{ev[:120]}")
        lines.append("-" * 48)
        agg = self.aggregation
        if agg.get("total_score") is not None:
            lines.append(f"加权总分：{agg['total_score']} / 100    等级：{agg['grade']}    结论：{agg['verdict']}")
        else:
            lines.append(f"总评结论：{agg.get('verdict')}（{agg.get('reason','')}）")
        if self.arbitration:
            lines.append(f"需仲裁维度：{', '.join(self.arbitration)}")
        if self.suggestions:
            lines.append("-" * 48)
            lines.append("改进建议：")
            for s in self.suggestions:
                lines.append(f"  - {s}")
        lines.append("=" * 48)
        return "\n".join(lines)


def evaluate(path: str, cfg: Hy3Config, ctx: Optional[EvalContext] = None,
             kb: Optional[KnowledgeBase] = None) -> Report:
    ctx = ctx or EvalContext()
    client = Hy3Client(cfg)
    if kb is None:
        kb = KnowledgeBase([])

    parsed: ParsedDoc = parse_file(path)
    context = {"grade": ctx.grade, "version": ctx.version,
               "topic": ctx.topic, "period": ctx.period}

    # 1) 事实 Judge：知识准入（G0）+ 学段适配（维度 3）
    fact = run_judge_group(client, "fact", parsed.text, context, kb)
    admission = fact.get("admission", "PASS")
    redline = bool(fact.get("redline", False))
    scores: Dict[str, Any] = dict(fact.get("scores", {}))
    suggestions: List[str] = list(fact.get("suggestions", []))
    kb_hits = len(kb.retrieve(parsed.text, top_k=5)) if kb.entries else 0

    report = Report(admission=admission, redline=redline, scores=scores,
                    parse={"parse_status": parsed.parse_status,
                           "parse_confidence": parsed.parse_confidence,
                           "notes": parsed.notes},
                    kb_hits=kb_hits)

    # 2) 知识准入未通过：只输出知识诊断，不生成教学质量总分
    if admission != "PASS":
        report.aggregation = aggregate(admission, redline, scores)
        report.suggestions = suggestions
        return report

    # 3) 教学设计 Judge（1/4/7/8/9）+ 表达与安全 Judge（5/6/A）
    design = run_judge_group(client, "design", parsed.text, context, kb)
    expr = run_judge_group(client, "expression_safety", parsed.text, context, kb)
    scores.update(design.get("scores", {}))
    scores.update(expr.get("scores", {}))
    suggestions.extend(design.get("suggestions", []))
    suggestions.extend(expr.get("suggestions", []))
    if expr.get("redline"):
        redline = True
    report.redline = redline
    report.scores = scores
    report.suggestions = suggestions

    # 4) 复核 Judge：检查证据完整性，标记需仲裁项
    review = multimodal_cross_check(client, {"scores": scores}, parsed.text, context)
    report.arbitration = review.get("needs_arbitration", [])

    # 5) 确定性聚合
    report.aggregation = aggregate(admission, redline, scores)
    return report


def evaluate_from_text(text: str, cfg: Hy3Config, ctx: Optional[EvalContext] = None,
                       kb: Optional[KnowledgeBase] = None) -> Report:
    """便于 Web / 测试直接传入文本。"""
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmppath = f.name
    try:
        return evaluate(tmppath, cfg, ctx, kb)
    finally:
        os.unlink(tmppath)
