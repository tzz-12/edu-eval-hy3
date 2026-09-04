"""双采样自一致（层内）——正式架构模块。

机制（用户提案，Phase 1 一致性实验验证有效）：
  每个 Judge 分别调两次 API，两次用不同「审视视角」制造**真实**分歧：
  - 同一维度两次分差 |Δ| ≤ 阈值 → 直接取平均（四舍五入）；
  - |Δ| > 阈值，或任一次为 ne / 缺失 → 引入**层内采样仲裁员**，
    综合两次判断的推理与 evidence、回看原文，给出该维度最终裁定分。

与 ArbitrateJudge 的分工（两者不可互相替代）：
  - ArbitrateJudge 解决「不同 Judge 之间」的维度分歧（跨层）；
  - SampleArbitrator 解决「同一 Judge 两次采样」的分歧（层内自一致）。

多样性来源：prompt 视角扰动（strict_rubric / learner_view）。
因指令进入 user_prompt，而 cache_key 对**渲染后的完整提示**取哈希
（P0-10 · E），两次采样自动换键、互不串味，无需额外的缓存隔离。

为什么必须接入正式架构（真实跑数据支撑，results/calibration_v3/）：
  40 个维度对中 28 个两次完全一致（70%），最大分差达 4（平面镶嵌 fact 维度 2：
  1 vs 5）；FactJudge 最不稳定（分差均值 0.75）。即 Judge 输出带**真实方差**，
  单次采样的分数不可复现。双采样把方差显式化：一致则取平均降噪，
  分歧则交仲裁裁决，并把「两次准入不一致」保守降级为 NE，
  宁可不出分，也不给一个可能错误的分。
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from edu_eval.eval import dimensions as D
from edu_eval.eval.judges.base import BaseJudge

LENS_A = "strict_rubric"
LENS_B = "learner_view"
DEFAULT_THRESHOLD = 1  # |Δ| ≤ 1 取平均；>1 触发层内仲裁


class SampleArbitrator(BaseJudge):
    """层内采样仲裁员：综合同一维度两次采样的判断与原文，给出最终裁定分。"""

    role = "sample_arbitrator"
    system = (
        "你是双采样分歧仲裁员。两位独立裁判对同一教学设计的同一维度给出了不同评分与推理，"
        "请你综合双方证据、推理链条与原文，给出该维度的最终裁定分数。"
        "必须引用原文片段作为裁定依据。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "",
                          rule_evidence: str = "") -> str:
        did = context["_arb_dim"]
        a_raw = context["_arb_a"]
        b_raw = context["_arb_b"]
        dim = D.get_dimension(did)
        meta = (f"用户声明：年级={context.get('grade', '未声明')}；"
                f"版本={context.get('version', '未声明')}；"
                f"课题={context.get('topic', '未声明')}")
        return (
            f"{meta}\n\n"
            f"【争议维度】维度 {did} {dim.name}\n锚点：\n{dim.anchors}\n\n"
            f"【待评估教学设计正文】\n{text}\n\n"
            f"【裁判 A（{LENS_A} 视角）的判断】\n{a_raw}\n\n"
            f"【裁判 B（{LENS_B} 视角）的判断】\n{b_raw}\n\n"
            "两位裁判在上述维度上评分不一致（或一方标记为不可判定）。请你：\n"
            "1) 对照原文核查双方各自引用的 evidence 是否真实存在、是否被恰当解读；\n"
            "2) 评估双方推理链条的合理性；\n"
            "3) 给出该维度的最终裁定分数（1-5 整数；若确实无法从原文判定则 ne=true）。\n\n"
            "返回严格 JSON："
            "{\"score\":1-5,\"ne\":bool,"
            "\"evidence\":\"裁定所依据的原文片段\",\"rationale\":\"综合双方的裁定理由\"}"
        )

    def _output_valid(self, data: Dict[str, Any]) -> bool:
        # 注意：role 不在 JUDGE_GROUPS 中，基类多维结构校验自动豁免，
        # 这里只需校验「单维度裁定」形态（顶层 score / ne）。
        if data.get("_parse_failed"):
            return False
        if data.get("ne"):
            return True  # ne 是合法裁定
        s = data.get("score")
        return isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5


def new_stats(threshold: int = DEFAULT_THRESHOLD) -> Dict[str, Any]:
    return {"dims_total": 0, "diffs": [], "arbitrations": 0,
            "threshold": threshold}


def _num(d: Optional[Dict[str, Any]]) -> Optional[int]:
    """取维度判定中的数值分；ne / 缺失 → None。

    兼容数值字符串（"3"）：解析层已统一归一化（_coerce_score），
    但双采样也会处理缓存回放等旁路数据，此处再兜一层。
    """
    if not isinstance(d, dict):
        return None
    if d.get("ne"):
        return None
    s = d.get("score")
    if isinstance(s, bool):
        return None
    if isinstance(s, int):
        return s
    if isinstance(s, float):
        return int(round(s))
    if isinstance(s, str):
        try:
            return int(s.strip())
        except ValueError:
            try:
                return int(round(float(s.strip())))
            except ValueError:
                return None
    return None


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _arbitrate(arb: SampleArbitrator, did: str, sa: Any, sb: Any,
               text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    ctx2 = dict(ctx)
    ctx2["_arb_dim"] = did
    ctx2["_arb_a"] = json.dumps(sa, ensure_ascii=False)
    ctx2["_arb_b"] = json.dumps(sb, ensure_ascii=False)
    out = arb.run(text, ctx2)
    return {
        "score": out.get("score"),
        "ne": bool(out.get("ne")),
        "evidence": out.get("evidence", ""),
        "rationale": out.get("rationale", ""),
    }


def _final_from_arb(arb_out: Dict[str, Any], sa: Any, sb: Any,
                    resolution: str) -> Dict[str, Any]:
    return {
        "score": arb_out.get("score"),
        "ne": bool(arb_out.get("ne")),
        "evidence": arb_out.get("evidence", ""),
        "rationale": arb_out.get("rationale", ""),
        "resolution": resolution,
        "a": (sa or {}).get("score") if isinstance(sa, dict) else None,
        "b": (sb or {}).get("score") if isinstance(sb, dict) else None,
    }


def resolve_dim(did: str, sa: Any, sb: Any, text: str, ctx: Dict[str, Any],
                arb: SampleArbitrator, threshold: int,
                stats: Dict[str, Any]) -> Dict[str, Any]:
    """对一个维度做双采样冲突消解，返回最终判定 dict。"""
    stats["dims_total"] += 1
    a_num = _num(sa)
    b_num = _num(sb)
    if a_num is not None and b_num is not None:
        diff = abs(a_num - b_num)
        stats["diffs"].append(diff)
        if diff <= threshold:
            return {
                "score": _round_half_up((a_num + b_num) / 2.0),
                "ne": False,
                "evidence": (sa if a_num >= b_num else sb).get("evidence", ""),
                "resolution": "average", "a": a_num, "b": b_num, "diff": diff,
            }
        arb_out = _arbitrate(arb, did, sa, sb, text, ctx)
        stats["arbitrations"] += 1
        return _final_from_arb(arb_out, sa, sb, "arbitrated")
    # 任一为 ne/缺失 → 「不确定性不一致」，同样交仲裁
    arb_out = _arbitrate(arb, did, sa, sb, text, ctx)
    stats["arbitrations"] += 1
    return _final_from_arb(arb_out, sa, sb, "arbitrated_ne")


def _merge_suggestions(ra: Dict[str, Any], rb: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for s in list(ra.get("suggestions") or []) + list(rb.get("suggestions") or []):
        if s and s not in out:
            out.append(s)
    return out


def dual_sample_judge(judge: BaseJudge, text: str, context: Dict[str, Any],
                      *, kb_context: str = "", rule_evidence: str = "",
                      arb: SampleArbitrator,
                      threshold: int = DEFAULT_THRESHOLD,
                      stats: Optional[Dict[str, Any]] = None):
    """对一个 Judge 做双采样，返回与「单次 run()+normalize()」同构的结果。

    返回结构刻意与单采样保持一致（admission/redline/scores/suggestions），
    编排层只需替换调用点，不必改动后续的复核/仲裁/聚合逻辑。
    额外在 scores 每个维度上附加 resolution/a/b/diff，供一致性分析溯源。

    admission 的合并口径（保守）：两次视角自报不一致 → NE。
    真实跑观察到此情形（正方形、平面镶嵌），它本身就是强非确定性信号：
    同一份材料两次采样连「是否准入」都判不一致时，给分不可信，
    按量规宁可 NE 也不出总分。
    """
    if stats is None:
        stats = new_stats(threshold)
    ctx_a = {**context, "_lens": LENS_A}
    ctx_b = {**context, "_lens": LENS_B}

    ra = judge.normalize(judge.run(text, ctx_a, kb_context=kb_context,
                                   rule_evidence=rule_evidence))
    rb = judge.normalize(judge.run(text, ctx_b, kb_context=kb_context,
                                   rule_evidence=rule_evidence))
    sa = ra.get("scores") or {}
    sb = rb.get("scores") or {}

    # 以本角色应评维度为准，两侧多出来的维度也不丢弃（模型偶发多评）
    dims = sorted(D.JUDGE_GROUPS.get(judge.role) or set())
    for did in sorted(set(sa) | set(sb)):
        if did not in dims:
            dims.append(did)

    final: Dict[str, Any] = {}
    for did in dims:
        final[did] = resolve_dim(did, sa.get(did), sb.get(did), text, context,
                                 arb, threshold, stats)

    adm_a = ra.get("admission")
    adm_b = rb.get("admission")
    out: Dict[str, Any] = {
        "admission": adm_a if adm_a == adm_b else "NE",
        "redline": bool(ra.get("redline")) or bool(rb.get("redline")),
        "scores": final,
        "suggestions": _merge_suggestions(ra, rb),
        "_dual_sample": {
            "lens_a": LENS_A, "lens_b": LENS_B, "threshold": threshold,
            "admission_a": adm_a, "admission_b": adm_b,
            "admission_consistent": adm_a == adm_b,
        },
    }
    # 两次采样都解析失败才整体判定失败：单次失败已被另一次采样兜住
    if ra.get("_parse_failed") and rb.get("_parse_failed"):
        out["_parse_failed"] = True
        out["_raw_excerpt"] = ra.get("_raw_excerpt", "")
    return out, stats


def summarize(stats: Dict[str, Any], n_samples: int = 1) -> Dict[str, Any]:
    diffs = stats["diffs"]
    total = stats["dims_total"]
    arb_n = stats["arbitrations"]
    return {
        "n_samples": n_samples,
        "dims_total": total,
        "threshold": stats["threshold"],
        "avg_path": total - arb_n,
        "arbitrated": arb_n,
        "disagreement_rate": round(arb_n / total, 3) if total else 0.0,
        "numeric_paired": len(diffs),
        "diff_mean": round(sum(diffs) / len(diffs), 3) if diffs else 0.0,
        "diff_max": max(diffs) if diffs else 0,
        "diff_zero": sum(1 for d in diffs if d == 0),
        "diff_gt1": sum(1 for d in diffs if d > 1),
    }
