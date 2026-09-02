"""评估主流程入口（向后兼容外壳）：内部委托 Orchestrator 编排。

编排顺序：解析 → 规则层 → 知识准入 → 多 Judge 评分 → 复核/仲裁 → 聚合 → 报告。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import Hy3Config
from ..parse.parsers import ParsedDoc, parse_file
from . import dimensions as D
from .knowledge_base import KnowledgeBase
from .orchestrator import Orchestrator, load_kb_assets


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
    #: P0-10 · B：规则层结构化结果不再被外壳丢弃（fail/warn/ne 全量留痕）
    rules: Dict[str, Any] = field(default_factory=dict)
    #: P0-10 · G/A：资产装载与环境告警（替代此前的静默降级）
    warnings: List[str] = field(default_factory=list)

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
            "rules": self.rules,
            "warnings": self.warnings,
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
        # P0-10 · B：规则层确定性结论进入文本报告
        rules = self.rules or {}
        if rules:
            lines.append("-" * 48)
            lines.append(f"规则层（零 LLM）：G0 判定 {rules.get('g0_rule_verdict', 'NE')}，"
                         f"共 {len(rules.get('findings', []))} 条发现")
            for f in rules.get("findings", []):
                mark = {"fail": "✗", "warn": "△", "ne": "·", "pass": "✓"}.get(
                    f.get("verdict"), "·")
                ev = (f.get("evidence") or f.get("reason") or "")[:80]
                lines.append(f"  {mark} [{f.get('rule_id')}] {ev}")
        if self.warnings:
            lines.append("-" * 48)
            lines.append("环境告警：")
            for w in self.warnings:
                lines.append(f"  ! {w}")
        if self.suggestions:
            lines.append("-" * 48)
            lines.append("改进建议：")
            for s in self.suggestions:
                lines.append(f"  - {s}")
        lines.append("=" * 48)
        return "\n".join(lines)


def evaluate(path: str, cfg: Hy3Config, ctx: Optional[EvalContext] = None,
             kb: Optional[KnowledgeBase] = None) -> Report:
    """评估入口。

    P0-10 修复：
    - 适配 load_kb_assets 的新签名（返回 4 元组，含告警列表）；
    - kb 参数仅覆盖知识库本体，年级映射与检索器**始终装载**——
      此前传入 kb 时 gm/retriever 被硬置 None，规则层静默失效。
    """
    ctx = ctx or EvalContext()
    kb_loaded, gm, retriever, warns = load_kb_assets()
    if kb is not None:
        kb_loaded = kb

    parsed: ParsedDoc = parse_file(path)
    context = {"grade": ctx.grade, "version": ctx.version,
               "topic": ctx.topic, "period": ctx.period}

    orch = Orchestrator(cfg, kb=kb_loaded, grade_map=gm, retriever=retriever,
                        warnings=warns)
    result = orch.run(parsed, context)
    return _to_report(result)


def _to_report(result: Dict[str, Any]) -> Report:
    return Report(
        admission=result["admission"],
        redline=result["redline"],
        scores=result["scores"],
        suggestions=result["suggestions"],
        aggregation=result["aggregation"],
        parse=result["parse"],
        arbitration=result.get("arbitration", []),
        kb_hits=result.get("kb_hits", 0),
        rules=result.get("rules") or {},
        warnings=result.get("warnings") or [],
    )


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
