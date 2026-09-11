"""评估主流程入口（向后兼容外壳）：内部委托 Orchestrator 编排。

编排顺序：解析 → 规则层 → 知识准入 → 多 Judge 评分 → 复核/仲裁 → 聚合 → 报告。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from ..config import Hy3Config
from ..parse.parsers import ParsedDoc, parse_file
from . import dimensions as D
from .knowledge_base import KnowledgeBase
from .dual_sample import DEFAULT_THRESHOLD
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
    #: 双采样自一致统计（默认开启；--no-dual-sample 关闭时为空 dict）
    dual_sample: Dict[str, Any] = field(default_factory=dict)
    #: 裁判元信息：这份报告由哪个模型 / 端点 / 参数产出。
    #:
    #: 为什么必须留痕：课题要求「全程通过 API 调用 Hy3」，而报告本身若不含
    #: 模型名，评审无法核实、事后也无法区分换模型前后的产物——此前 3 份演示
    #: 报告的 JSON 里搜不到任何模型信息，等于证据链断了。
    judge: Dict[str, Any] = field(default_factory=dict)

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
            "dual_sample": self.dual_sample,
            "judge": self.judge,
        }

    def to_text(self) -> str:
        lines = []
        lines.append("=" * 48)
        lines.append("EduEval 评估报告（基于混元 Hy3 · 个人/活动作品）")
        lines.append("=" * 48)
        jm = self.judge or {}
        if jm.get("model"):
            lines.append(
                f"裁判模型：{jm['model']}"
                + (f" @ {jm['endpoint_host']}" if jm.get("endpoint_host") else "")
                + ("（mock 演示数据）" if jm.get("mock") else "")
            )
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
        # 双采样自一致：把「分数有多可信」显式呈现，而不是只给一个静默的数字
        ds = self.dual_sample or {}
        if ds.get("dims_total"):
            lines.append(
                f"双采样自一致：{ds['dims_total']} 维度，平均 {ds['avg_path']} / "
                f"层内仲裁 {ds['arbitrated']}（分歧率 {ds['disagreement_rate']:.0%}），"
                f"两次分差均值 {ds['diff_mean']}、最大 {ds['diff_max']}"
                f"（满分完全一致 {ds['diff_zero']}/{ds['numeric_paired']}）")
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


def _build_judge_meta(cfg: Hy3Config, *, elapsed_s: float,
                      dual_sample: bool, dual_threshold: int) -> Dict[str, Any]:
    """记录产出这份报告的裁判身份与关键参数（报告自证的唯一依据）。

    只写入**不含密钥**的信息：模型名、端点主机名、mock 标记与采样参数。
    端点只留 host（如 tokenhub.tencentmaas.com），不带路径与凭据。
    """
    try:
        host = urlparse(cfg.base_url or "").netloc or (cfg.base_url or "")
    except ValueError:  # pragma: no cover - 畸形 URL 不该让评测挂掉
        host = cfg.base_url or ""
    return {
        "model": cfg.model,
        "endpoint_host": host,
        "mock": bool(cfg.mock),
        "dual_sample": bool(dual_sample),
        "dual_threshold": dual_threshold,
        "max_tokens": cfg.max_tokens,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed_s, 1),
    }


def evaluate(path: str, cfg: Hy3Config, ctx: Optional[EvalContext] = None,
             kb: Optional[KnowledgeBase] = None, denoise: bool = False,
             dual_sample: bool = True,
             dual_threshold: int = DEFAULT_THRESHOLD) -> Report:
    """评估入口。

    P0-10 修复：
    - 适配 load_kb_assets 的新签名（返回 4 元组，含告警列表）；
    - kb 参数仅覆盖知识库本体，年级映射与检索器**始终装载**——
      此前传入 kb 时 gm/retriever 被硬置 None，规则层静默失效。

    `denoise`：对文本类输入（.md/.txt）额外跑一遍抽取降噪。PDF 路径
    默认降噪（parsers.parse_file 内部处理）。详见 parse.layout.denoise_text。
    """
    t0 = time.time()
    ctx = ctx or EvalContext()
    kb_loaded, gm, retriever, warns = load_kb_assets()
    if kb is not None:
        kb_loaded = kb

    parsed: ParsedDoc = parse_file(path, denoise=denoise)
    context = {"grade": ctx.grade, "version": ctx.version,
               "topic": ctx.topic, "period": ctx.period}

    orch = Orchestrator(cfg, kb=kb_loaded, grade_map=gm, retriever=retriever,
                        warnings=warns, dual_sample=dual_sample,
                        dual_threshold=dual_threshold)
    result = orch.run(parsed, context)
    report = _to_report(result)
    report.judge = _build_judge_meta(cfg, elapsed_s=time.time() - t0,
                                     dual_sample=dual_sample,
                                     dual_threshold=dual_threshold)
    return report


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
        dual_sample=result.get("dual_sample") or {},
    )


def evaluate_from_text(text: str, cfg: Hy3Config, ctx: Optional[EvalContext] = None,
                       kb: Optional[KnowledgeBase] = None, denoise: bool = False,
                       dual_sample: bool = True,
                       dual_threshold: int = DEFAULT_THRESHOLD) -> Report:
    """便于 Web / 测试直接传入文本。"""
    import tempfile, os
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(text)
        tmppath = f.name
    try:
        return evaluate(tmppath, cfg, ctx, kb, denoise=denoise,
                        dual_sample=dual_sample, dual_threshold=dual_threshold)
    finally:
        os.unlink(tmppath)
