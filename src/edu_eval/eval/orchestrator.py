"""评估编排器：解析 → 规则层 → G0（Judge）→ 教学评分 → 复核 → 仲裁 → 聚合。

P0-7 相比旧 run_eval.evaluate 的增强：
1. 规则层前置：公式错误 / 年级超纲的硬证据先于任何 LLM 调用产生
   （M1 里程碑的编排化落地）。
2. ocr_required 解析状态 → 直接 NE，不进入评分。
3. 五类 Judge 拆分（judges/*），带磁盘缓存。
4. 复核确定性预检（证据是否在原文）+ LLM 仲裁兜底。

P0-10 修复：
· A  知识库装载失败不再静默降级，改为记录到 report["warnings"]；
· B  规则层的结构化结果 report["rules"] 不再在 Report 外壳里被丢掉；
· C  kb_hits 计算不再依赖 `x and len(...) or 0` 这种类型不稳定的写法；
· E  缓存键改为对完整渲染提示取哈希（见 cache.py）；
· G  知识库与缓存路径经 paths.py 解析，不再依赖当前工作目录。

run_eval.evaluate 保持向后兼容：内部委托本编排器。
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

from edu_eval.config import Hy3Config
from edu_eval.hy3 import Hy3Client
from edu_eval.kb.grade_map import GradeMap
from edu_eval.kb.retriever import KBRetriever
from edu_eval.parse.parsers import ParsedDoc, parse_file
from edu_eval.eval import dimensions as D
from edu_eval.eval.aggregator import aggregate
from edu_eval.eval.cache import JudgeCache
from edu_eval.eval.rules import RuleEngine
from edu_eval.eval.judges.fact import FactJudge
from edu_eval.eval.judges.design import DesignJudge
from edu_eval.eval.judges.expression_safety import ExpressionSafetyJudge
from edu_eval.eval.judges.review import ReviewJudge
from edu_eval.eval.judges.arbitrate import ArbitrateJudge
from edu_eval.eval.knowledge_base import KnowledgeBase


class Orchestrator:
    def __init__(self, cfg: Hy3Config, kb: Optional[KnowledgeBase] = None,
                 grade_map: Optional[GradeMap] = None,
                 retriever: Optional[KBRetriever] = None,
                 use_cache: bool = True,
                 warnings: Optional[List[str]] = None):
        self.cfg = cfg
        self.client = Hy3Client(cfg)
        self.kb = kb or KnowledgeBase([])
        self.grade_map = grade_map
        self.retriever = retriever
        self.cache = JudgeCache(enabled=use_cache)
        self.rules = RuleEngine(grade_map=grade_map, retriever=retriever)
        #: 环境与资产装载告警（替代此前的静默降级）
        self.warnings: List[str] = list(warnings or [])
        if grade_map is None:
            self.warnings.append(
                "年级映射表未加载：学段适配（维度 3）的确定性证据不可用，"
                "相关判定退化为 NE。请运行 `python -m edu_eval.kb.grade_map` 重建。")
        if retriever is None:
            self.warnings.append(
                "概念检索器未加载：无法从正文抽取概念，年级比对与知识库上下文将受限。"
                "请运行 `python -m edu_eval.kb.ingest` 重建。")
        if not self.kb.entries:
            self.warnings.append(
                "知识库为空：fact Judge 缺少可引用的条目，G0 判定退化为 NE。")

        self.j_fact = FactJudge(self.client, self.cache)
        self.j_design = DesignJudge(self.client, self.cache)
        self.j_expr = ExpressionSafetyJudge(self.client, self.cache)
        self.j_review = ReviewJudge(self.client, self.cache)
        self.j_arbit = ArbitrateJudge(self.client, self.cache)

    # ------------------------------------------------------------------
    def run(self, parsed: ParsedDoc, context: Dict[str, Any]) -> Dict[str, Any]:
        """对已解析文档执行完整评估，返回报告 dict。"""
        report: Dict[str, Any] = {
            "parse": {
                "parse_status": parsed.parse_status,
                "parse_confidence": parsed.parse_confidence,
                "notes": parsed.notes,
            },
            "rules": None, "admission": "NE", "redline": False,
            "scores": {}, "suggestions": [], "aggregation": {},
            "arbitration": [], "kb_hits": 0,
            "warnings": list(self.warnings),
            "cache_stats": self.cache.stats(),
        }
        text = parsed.text

        # 0) 解析不可靠 → NE（不猜测）
        if parsed.parse_status == "ocr_required" or not text.strip():
            report["aggregation"] = aggregate("NE", False, {})
            report["warnings"] = list(self.warnings)
            return report

        # 1) 规则层（确定性，零 LLM）
        # 注意：不传 concept_ids —— 让规则层自行把概念分为「文本显式出现」
        # 与「检索联想」两级，前者越界判 FAIL、后者判 WARN。若在此处把
        # 检索结果一律传入，会全部落到宽松档，显式超纲将漏判。
        rule_rep = self.rules.evaluate(
            text, declared_grade=context.get("grade", ""),
            retriever=self.retriever)
        report["rules"] = {
            "g0_rule_verdict": rule_rep["g0_rule_verdict"],
            "summary": rule_rep["summary"],
            "findings": rule_rep["findings"],
        }

        # 2) G0：规则层已 FAIL → 直接不通过（红线一票否决，无需 LLM）
        scores: Dict[str, Any] = {}
        suggestions: List[str] = []
        if rule_rep["g0_rule_verdict"] == "FAIL":
            report["admission"] = "FAIL"
            fails = [f for f in rule_rep["findings"] if f["verdict"] == "fail"]
            suggestions = [f"规则层检出确定性知识错误：{f['evidence']}" for f in fails]
            report["suggestions"] = suggestions
            report["aggregation"] = aggregate("FAIL", False, {})
            report["warnings"] = list(self.warnings)
            return report

        # 3) 事实 Judge（G0 + 维度 3）
        #    规则层的确定性判定作为硬证据注入，否则维度 3 完全依赖 LLM，
        #    规则层已算出的超纲结论被浪费。
        kb_hits, kb_context = self._kb_context(text)
        rule_evidence = self._format_rule_evidence(rule_rep)
        fact = self.j_fact.run(text, context, kb_context=kb_context,
                              rule_evidence=rule_evidence)
        fact = self.j_fact.normalize(fact)
        report["kb_hits"] = kb_hits
        admission = fact.get("admission", "PASS")
        redline = bool(fact.get("redline", False))
        scores.update(fact.get("scores", {}))
        suggestions.extend(fact.get("suggestions", []))

        if admission != "PASS":
            report["admission"] = admission
            report["redline"] = redline
            report["scores"] = scores
            report["suggestions"] = suggestions
            report["aggregation"] = aggregate(admission, redline, scores)
            report["warnings"] = list(self.warnings)
            return report

        # 4) 教学设计 Judge + 表达与安全 Judge
        design = self.j_design.run(text, context)
        expr = self.j_expr.run(text, context)
        design = self.j_design.normalize(design)
        expr = self.j_expr.normalize(expr)
        scores.update(design.get("scores", {}))
        scores.update(expr.get("scores", {}))
        suggestions.extend(design.get("suggestions", []))
        suggestions.extend(expr.get("suggestions", []))
        if expr.get("redline"):
            redline = True

        # 5) 复核（确定性预检）→ 仲裁（争议维度）
        review = self.j_review.review(scores, text)
        disputed = review["needs_arbitration"]
        if disputed:
            try:
                arb = self.j_arbit.run(
                    text,
                    {**context, "_disputed": disputed},
                    kb_context=json.dumps(
                        {"scores": {d: scores.get(d) for d in disputed},
                         "review_problems": review["problems"]},
                        ensure_ascii=False)[:4000],
                )
                arb_scores = arb.get("scores", {})
                for did, s in arb_scores.items():
                    if isinstance(s, dict):
                        s.setdefault("arbitrated", True)
                        scores[did] = s
            except Exception:  # 仲裁失败保留主判（记录争议）
                pass
            report["arbitration"] = disputed

        # 6) 聚合
        report["admission"] = "PASS"
        report["redline"] = redline
        report["scores"] = scores
        report["suggestions"] = suggestions
        report["aggregation"] = aggregate("PASS", redline, scores)
        report["cache_stats"] = self.cache.stats()
        report["warnings"] = list(self.warnings)
        return report

    # ------------------------------------------------------------------
    def _match_concepts(self, text: str) -> List[str]:
        """检索命中的概念 id（供维度 3 年级比对）。"""
        if not self.retriever:
            return []
        try:
            # 取教学设计中的高频名词短语做检索（取正文前 4000 字避免全量）
            hits = self.retriever.search(text[:4000], top_k=10)
            return [h.id for h in hits]
        except Exception:
            return []

    @staticmethod
    def _format_rule_evidence(rule_rep: dict) -> str:
        """把规则层 findings 渲染为给 Judge 的硬证据文本。

        只输出有信息量的项：显式/联想的年级判定、公式核验、豁免留痕。
        纯 pass 与空判定不占用提示预算。
        """
        lines = []
        for f in rule_rep.get("findings", []):
            rid, verdict, ev = (f.get("rule_id", ""), f.get("verdict", ""),
                                (f.get("evidence") or "").strip())
            if rid.startswith("R-GRADE"):
                if rid == "R-GRADE-EXPL" and verdict == "fail":
                    lines.append(f"- 超纲（概念在原文中显式出现）：{ev}")
                elif rid == "R-GRADE-EXPL" and verdict == "pass":
                    lines.append("- 原文显式出现的概念均在声明年级范围内")
                elif rid == "R-GRADE-ASSOC" and verdict == "warn":
                    lines.append(f"- 疑似超纲（仅由检索联想，原文未直接出现）：{ev}")
                elif rid == "R-GRADE-EXEMPT":
                    lines.append(f"- 豁免（图谱收录偏差，不作为超纲依据）：{ev}")
                elif rid == "R-GRADE" and verdict == "ne":
                    lines.append(f"- 年级判定不可用：{ev}")
                # 前置知识是**正向信号**：明确告诉 Judge 不要据此判超纲
                # （P0-10 · D：早于声明年级的概念曾被误判为超纲）
                if rid in ("R-GRADE-EXPL", "R-GRADE-ASSOC"):
                    earlier = (f.get("detail") or {}).get("analysis", {}).get("earlier")
                    if earlier:
                        names = "、".join(i["name"] for i in earlier[:6])
                        lines.append(
                            f"- 前置知识（早于声明年级，属正常引用，"
                            f"**不得据此判超纲**）：{names}")
            elif rid == "R-FORMULA" and verdict == "pass":
                lines.append(f"- 公式核验通过：{ev}")
        return "\n".join(lines)

    def _kb_context(self, text: str):
        """构建给 fact Judge 的知识库上下文。

        返回 (命中条数, 提示文本)。
        优先用概念检索器召回的概念名查表；没有检索器时退回「在正文中查找
        知识条目名称」。绝不把整篇文档当查询 —— 那样词元集合过大，
        top_k 会退化为随机抽样。
        """
        if not self.kb.entries:
            return 0, ""
        names: List[str] = []
        if self.retriever is not None:
            try:
                names = [h.name for h in self.retriever.search(text[:4000], top_k=8)]
            except Exception as e:  # 检索失败不应中断评测，但必须留痕
                self.warnings.append(f"概念检索失败，知识库上下文降级：{type(e).__name__}")
        entries = self.kb.retrieve_for_text(text[:4000], top_k=5,
                                            concept_names=names or None)
        return len(entries), self.kb.format_for_prompt(entries)


def load_kb_assets(retriever_enabled: bool = True, warnings: Optional[List[str]] = None):
    """加载知识库资产。

    P0-10 · A/G 修复：
    · 路径经 paths.py 解析，**不再依赖当前工作目录**；
    · 装载失败不再静默降级为 None，而是把原因写入 warnings 返回给调用方。
    """
    from .. import paths as P

    warns: List[str] = list(warnings or [])
    kb = None
    gm = None
    retriever = None

    kb, kb_warns = KnowledgeBase.load_safe(P.kb_jsonl())
    warns.extend(kb_warns)

    grade_json = P.grade_json()
    if os.path.exists(grade_json):
        try:
            gm = GradeMap.load(grade_json)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            warns.append(f"年级映射表装载失败：{e}")
    else:
        warns.append(f"年级映射表不存在：{grade_json}"
                     f"（请运行 `python -m edu_eval.kb.grade_map` 重建）")

    db_path = P.kb_db()
    if retriever_enabled:
        if os.path.exists(db_path):
            try:
                retriever = KBRetriever(db_path, P.kb_jsonl())
            except (OSError, sqlite3.Error, ValueError) as e:
                warns.append(f"概念检索器装载失败：{e}")
        else:
            warns.append(f"检索索引不存在：{db_path}"
                         f"（请运行 `python -m edu_eval.kb.ingest` 重建）")

    return kb, gm, retriever, warns
