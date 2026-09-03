#!/usr/bin/env python3
"""双采样自一致原型（Phase 1 一致性实验交付物之一）。

机制（用户提案）：
  每一层 Judge 分别调两次 API（两次用不同「审视视角」prompt 制造真实分歧），
  - 若同一维度两次分差 |Δ| ≤ 阈值 → 直接取平均；
  - 若 |Δ| > 阈值（或任一次为 ne / 缺失）→ 引入一个**层内采样仲裁员**，
    综合两次判断的推理与 evidence、回看原文，给出该维度最终裁定分。

与现有仲裁的区别：
  - ArbitrateJudge 解决「不同 Judge 之间维度分歧」（跨层）；
  - SampleArbitrator 解决「同一 Judge 两次采样分歧」（层内自一致）。这是新路径。

多样性来源（已与用户确认）：prompt 视角扰动。
  - LENS_A = strict_rubric（严格按量规逐条核对）
  - LENS_B = learner_view（切换到学习者/同行教师真实课堂视角）
  因指令进入 user prompt，cache_key 自动换键 → 两次采样缓存互不串味。

注（原型偏差，不影响结论）：原型对 4 份样本**无条件**跑全部三层 Judge，
不复刻生产「G0 失败即停」的短路；4 份样本在 v3 均 PASS 准入，故无影响。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Any, Dict, Optional

from edu_eval.config import Hy3Config
from edu_eval.hy3 import Hy3Client
from edu_eval.parse.parsers import parse_file
from edu_eval.eval.judges.base import BaseJudge
from edu_eval.eval.judges.fact import FactJudge
from edu_eval.eval.judges.design import DesignJudge
from edu_eval.eval.judges.expression_safety import ExpressionSafetyJudge
from edu_eval.eval.cache import JudgeCache
from edu_eval.eval import dimensions as D
from edu_eval.eval.aggregator import aggregate

LENS_A = "strict_rubric"
LENS_B = "learner_view"
DEFAULT_THRESHOLD = 1  # |Δ| ≤ 1 取平均；>1 触发仲裁

# (路径, 评测上下文) —— 与 run_calibration_v3.sh 完全一致，保证可比
SAMPLES = [
    ("data/samples/calibration_md/二次函数的图象和性质.md",
     dict(grade="九年级", version="人教版", topic="二次函数的图象和性质", period="1课时")),
    ("data/samples/calibration_md/平行四边形性质.md",
     dict(grade="八年级", version="人教版", topic="平行四边形的性质", period="1课时")),
    ("data/samples/calibration_md/丰富多彩的正方形.md",
     dict(grade="八年级", version="人教版", topic="正方形", period="1课时")),
    ("data/samples/calibration_md/平面镶嵌.md",
     dict(grade="八年级", version="人教版", topic="平面镶嵌", period="1课时")),
]


class CountingClient:
    """包裹真实/假客户端，统计真实 LLM 调用次数（含重试与仲裁）。"""

    def __init__(self, client: Hy3Client):
        self._c = client
        self.calls = 0

    @property
    def cfg(self):
        return self._c.cfg

    def judge(self, system: str, user: str, *, temperature=None, max_tokens=None) -> str:
        self.calls += 1
        return self._c.judge(system, user, temperature=temperature, max_tokens=max_tokens)


class SampleArbitrator(BaseJudge):
    """层内采样仲裁员：综合同一维度两次采样的判断与原文，给出最终裁定分。"""

    role = "sample_arbitrator"
    system = (
        "你是双采样分歧仲裁员。两位独立裁判对同一教学设计的同一维度给出了不同评分与推理，"
        "请你综合双方证据、推理链条与原文，给出该维度的最终裁定分数。"
        "必须引用原文片段作为裁定依据。"
    )

    def build_user_prompt(self, text: str, context: Dict[str, Any],
                          kb_context: str = "", rule_evidence: str = "") -> str:
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
        if data.get("_parse_failed"):
            return False
        if data.get("ne"):
            return True  # ne 是合法裁定
        s = data.get("score")
        return isinstance(s, int) and not isinstance(s, bool) and 1 <= s <= 5


def _num(d: Optional[Dict[str, Any]]) -> Optional[int]:
    """取维度判定中的数值分；ne / 缺失 → None。"""
    if not isinstance(d, dict):
        return None
    if d.get("ne"):
        return None
    s = d.get("score")
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return int(s)
    return None


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _dim_info(did: str):
    dim = D.get_dimension(did)
    return dim.name, dim.anchors


def _arbitrate(arb: SampleArbitrator, did: str, sa: Any, sb: Any,
               text: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    a_raw = json.dumps(sa, ensure_ascii=False)
    b_raw = json.dumps(sb, ensure_ascii=False)
    ctx2 = dict(ctx)
    ctx2["_arb_dim"] = did
    ctx2["_arb_a"] = a_raw
    ctx2["_arb_b"] = b_raw
    out = arb.run(text, ctx2)
    return {
        "score": out.get("score"),
        "ne": bool(out.get("ne")),
        "evidence": out.get("evidence", ""),
        "rationale": out.get("rationale", ""),
    }


def resolve_dim(did: str, sa: Any, sb: Any, text: str, ctx: Dict[str, Any],
                arb: SampleArbitrator, threshold: int, stats: Dict[str, Any]) -> Dict[str, Any]:
    """对一个维度做双采样冲突消解，返回最终判定 dict。"""
    stats["dims_total"] += 1
    a_num = _num(sa)
    b_num = _num(sb)
    if a_num is not None and b_num is not None:
        diff = abs(a_num - b_num)
        stats["diffs"].append(diff)
        if diff <= threshold:
            final = _round_half_up((a_num + b_num) / 2.0)
            return {
                "score": final, "ne": False,
                "evidence": (sa if a_num >= b_num else sb).get("evidence", ""),
                "resolution": "average", "a": a_num, "b": b_num, "diff": diff,
            }
        # 分差过大 → 仲裁
        arb_out = _arbitrate(arb, did, sa, sb, text, ctx)
        stats["arbitrations"] += 1
        return _final_from_arb(arb_out, sa, sb, "arbitrated")
    # 任一为 ne/缺失 → 视为「不确定性不一致」，同样仲裁
    arb_out = _arbitrate(arb, did, sa, sb, text, ctx)
    stats["arbitrations"] += 1
    return _final_from_arb(arb_out, sa, sb, "arbitrated_ne")


def _final_from_arb(arb_out: Dict[str, Any], sa: Any, sb: Any, resolution: str) -> Dict[str, Any]:
    return {
        "score": arb_out.get("score"),
        "ne": bool(arb_out.get("ne")),
        "evidence": arb_out.get("evidence", ""),
        "rationale": arb_out.get("rationale", ""),
        "resolution": resolution,
        "a": (sa or {}).get("score") if isinstance(sa, dict) else None,
        "b": (sb or {}).get("score") if isinstance(sb, dict) else None,
    }


def run_dual_sample(judge: BaseJudge, text: str, ctx: Dict[str, Any],
                    cache: JudgeCache, threshold: int, arb: SampleArbitrator,
                    stats: Dict[str, Any]):
    dims = D.JUDGE_GROUPS[judge.role]
    ctx_a = dict(ctx)
    ctx_a["_lens"] = LENS_A
    ctx_b = dict(ctx)
    ctx_b["_lens"] = LENS_B
    ra = BaseJudge.normalize(judge.run(text, ctx_a))
    rb = BaseJudge.normalize(judge.run(text, ctx_b))
    scores_a = ra.get("scores") or {}
    scores_b = rb.get("scores") or {}
    final: Dict[str, Any] = {}
    for did in dims:
        final[did] = resolve_dim(did, scores_a.get(did), scores_b.get(did),
                                  text, ctx, arb, threshold, stats)
    return final, ra, rb


def summarize(stats: Dict[str, Any], n_samples: int) -> Dict[str, Any]:
    diffs = stats["diffs"]
    total = stats["dims_total"]
    arb_n = stats["arbitrations"]
    avg_n = total - arb_n
    out = {
        "n_samples": n_samples,
        "dims_total": total,
        "threshold": stats["threshold"],
        "avg_path": avg_n,
        "arbitrated": arb_n,
        "disagreement_rate": round(arb_n / total, 3) if total else 0.0,
        "numeric_paired": len(diffs),
        "diff_mean": round(sum(diffs) / len(diffs), 3) if diffs else 0.0,
        "diff_max": max(diffs) if diffs else 0,
        "diff_zero": sum(1 for d in diffs if d == 0),
        "diff_gt1": sum(1 for d in diffs if d > 1),
    }
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="双采样自一致原型")
    p.add_argument("--out", default="results/calibration_v3/dual_sample.json",
                   help="统计结果输出路径（JSON）")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help="|Δ| 超过该值触发仲裁（默认 1）")
    p.add_argument("--no-cache", action="store_true", help="禁用缓存（每次真实调用）")
    p.add_argument("--cache-dir",
                   default=os.path.join("results", ".proto", "dual_sample_cache"),
                   help="原型专用缓存目录（避免污染主缓存）")
    p.add_argument("--selftest", action="store_true",
                   help="用假客户端制造真实分歧，验证平均/仲裁两条分支")
    args = p.parse_args(argv)

    if args.selftest:
        return _selftest()

    os.makedirs(os.path.dirname(args.cache_dir) or ".", exist_ok=True)
    cfg = Hy3Config.from_env()
    real_client = Hy3Client(cfg)
    client = CountingClient(real_client)
    cache = JudgeCache(enabled=not args.no_cache, cache_dir=args.cache_dir)
    arb = SampleArbitrator(client, cache)
    judges = [FactJudge(client, cache), DesignJudge(client, cache),
              ExpressionSafetyJudge(client, cache)]

    stats: Dict[str, Any] = {"dims_total": 0, "diffs": [], "arbitrations": 0,
                             "threshold": args.threshold, "samples": []}
    n = 0
    for path, ctx in SAMPLES:
        parsed = parse_file(path, denoise=True)
        if not parsed.text.strip():
            print(f"[skip] 空文本：{path}", file=sys.stderr)
            continue
        n += 1
        text = parsed.text
        final_scores: Dict[str, Any] = {}
        admission = "PASS"
        redline = False
        rec: Dict[str, Any] = {"file": os.path.basename(path), "judges": {}}
        for judge in judges:
            jname = judge.role
            fs, ra, rb = run_dual_sample(judge, text, ctx, cache, args.threshold,
                                         arb, stats)
            if jname == "fact":
                adm_a = ra.get("admission")
                adm_b = rb.get("admission")
                admission = adm_a if adm_a == adm_b else "NE"  # 两次准入不一致→保守
            if jname == "expression_safety":
                redline = bool(ra.get("redline")) or bool(rb.get("redline"))
            final_scores.update(fs)
            rec["judges"][jname] = fs
        agg = aggregate(admission, redline, final_scores)
        rec["admission"] = admission
        rec["redline"] = redline
        rec["aggregate"] = agg
        stats["samples"].append(rec)
        print(f"[{n}/{len(SAMPLES)}] {os.path.basename(path)}: "
              f"admission={admission} total={agg.get('total_score')} "
              f"grade={agg.get('grade')}", file=sys.stderr)

    summary = summarize(stats, n)
    report = {
        "config": {
            "lens_a": LENS_A, "lens_b": LENS_B, "threshold": args.threshold,
            "mock": cfg.mock, "model": cfg.model,
        },
        "summary": summary,
        "cost": {
            "api_calls": client.calls,
            "cache_hits": cache.hits,
            "cache_misses": cache.misses,
            "arbitrations": stats["arbitrations"],
            "base_judge_calls_nominal": 2 * 3 * n,  # 每层 2 次 × 3 层 × n 样本
        },
        "samples": stats["samples"],
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"summary": summary, "cost": report["cost"]},
                     ensure_ascii=False, indent=2))
    return 0


def _selftest() -> int:
    """用假客户端制造真实分歧，验证 average 与 arbitrated 两条分支。"""
    class FakeClient:
        def __init__(self):
            # 仅供 cache_key 取 temperature/model，不发起真实请求
            self.cfg = Hy3Config(base_url="https://example.invalid/v1",
                                 api_key="mock", model="hy3", mock=True)
            self.calls = 0

        def judge(self, system, user, *, temperature=None, max_tokens=None):
            self.calls += 1
            if "最终裁定分数" in user:  # SampleArbitrator 提示的特征串
                return json.dumps({"score": 4, "ne": False,
                                   "evidence": "仲裁：综合双方证据，A 偏严 B 偏宽折中",
                                   "rationale": "双方均引用真实片段，取中值"})
            ids = re.findall(r"维度\s*([0-9A-Za-z]+)", user)
            ids = [i for i in dict.fromkeys(ids) if i.lower() != "id"]
            if "学习者 / 同行教师" in user:  # learner_view 视角指令的特征串
                scores = {i: {"score": 5 if i in ("1", "5", "7", "A") else 3,
                              "evidence": "lv", "ne": False} for i in ids}
            else:
                scores = {i: {"score": 3, "evidence": "sr", "ne": False} for i in ids}
            return json.dumps({"admission": "PASS", "redline": False,
                               "scores": scores, "suggestions": []}, ensure_ascii=False)

    text = "教学目标：理解函数概念。教学过程：设问导入，分组探究。"
    client = CountingClient(FakeClient())  # type: ignore[arg-type]
    cache = JudgeCache(enabled=False)
    arb = SampleArbitrator(client, cache)
    judge = DesignJudge(client, cache)  # dims 1,4,7,8,9
    ctx = dict(grade="九年级", version="人教版", topic="函数", period="1课时")
    stats = {"dims_total": 0, "diffs": [], "arbitrations": 0, "threshold": 1}
    fs, _, _ = run_dual_sample(judge, text, ctx, cache, 1, arb, stats)
    print("=== 自测：DesignJudge 双采样（strict=3, learner: 1/5/7/A=5, 4/8/9=3）===")
    ok = True
    for did, r in fs.items():
        print(f"  维度{did}: a={r.get('a')} b={r.get('b')} → 最终={r.get('score')} "
              f"[{r.get('resolution')}]")
        if did in ("1", "7", "A"):
            # 分差 2 → 仲裁，应为 4
            if r.get("resolution") != "arbitrated" or r.get("score") != 4:
                ok = False
                print(f"    ✗ 维度{did} 期望仲裁得 4")
        else:
            # 分差 0 → 平均，应为 3
            if r.get("resolution") != "average" or r.get("score") != 3:
                ok = False
                print(f"    ✗ 维度{did} 期望平均得 3")
    print(f"api_calls={client.calls} arbitrations={stats['arbitrations']} "
          f"dims_total={stats['dims_total']} -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
