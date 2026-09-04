#!/usr/bin/env python3
"""双采样真实跑结果分析：从 dual_sample_real.json 提炼一致性结论。

修复口径：
- 仲裁维度 rec 不写 diff 字段（_final_from_arb 漏写），需按 a/b 重算；
- FactJudge 偶发返回 score 为字符串（"3"），_num 当 None，故 a/b 也需安全转 int；
- resolution 取值 average / arbitrated / arbitrated_ne 三类，后两者都计入仲裁。

用法:
  PYTHONPATH=src python scripts/analyze_dual.py <report.json> [--md out.md]
"""
from __future__ import annotations
import json, sys, argparse
from collections import defaultdict


def _to_int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def load(path: str):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def analyze(rpt: dict) -> dict:
    samples = rpt.get("samples", [])
    judges_of_interest = {"fact": "FactJudge(维度2/3)",
                          "design": "DesignJudge(1/4/7/8/9)",
                          "expression_safety": "ExprSafety(5/6/A)"}
    diff_by_judge = defaultdict(list)
    triggered = []          # 所有 resolution 以 arbitrated 开头的维度
    arbitrated_ne = []      # 其中任一方缺失/ne（真不确定性）
    hard_fail = []          # 连仲裁都没分数（a/b/score 全 None）
    ne_dims = []
    dim2_records = []
    diff_vals = []
    n_avg = 0
    pair_total = 0

    for s in samples:
        fname = s.get("file", "?")
        judges = s.get("judges", {})
        fact = judges.get("fact", {})
        d2 = fact.get("2", {})
        dim2_records.append((fname, _to_int(d2.get("score")),
                             s.get("admission"), s.get("redline")))
        for jk, dims in judges.items():
            for dim, rec in dims.items():
                pair_total += 1
                a = _to_int(rec.get("a"))
                b = _to_int(rec.get("b"))
                ne = bool(rec.get("ne"))
                res = rec.get("resolution")
                if a is not None and b is not None and not ne:
                    diff = abs(a - b)
                    diff_vals.append(diff)
                    diff_by_judge[jk].append(diff)
                else:
                    diff = None
                if res == "average":
                    n_avg += 1
                elif isinstance(res, str) and res.startswith("arbitrated"):
                    triggered.append((fname, jk, dim, a, b, diff, res))
                    if res == "arbitrated_ne":
                        arbitrated_ne.append((fname, jk, dim, a, b))
                if ne or (a is None and b is None and rec.get("score") is None):
                    hard_fail.append((fname, jk, dim, res))

    n_arbitrated = len(triggered)
    n_ne_arb = len(arbitrated_ne)
    n_zero = sum(1 for d in diff_vals if d == 0)
    n_gt1 = sum(1 for d in diff_vals if d > 1)
    mean_diff = (sum(diff_vals) / len(diff_vals)) if diff_vals else 0.0
    max_diff = max(diff_vals) if diff_vals else 0
    judge_stats = {}
    for jk, lst in diff_by_judge.items():
        if lst:
            judge_stats[jk] = {
                "n": len(lst),
                "mean": round(sum(lst) / len(lst), 3),
                "max": max(lst),
                "zero": sum(1 for d in lst if d == 0),
            }
    return {
        "summary": rpt.get("summary", {}),
        "n_samples": len(samples),
        "pair_total": pair_total,
        "diff_vals": diff_vals,
        "n_avg": n_avg,
        "n_arbitrated": n_arbitrated,
        "n_ne_arb": n_ne_arb,
        "hard_fail": hard_fail,
        "n_zero": n_zero,
        "n_gt1": n_gt1,
        "mean_diff": round(mean_diff, 3),
        "max_diff": max_diff,
        "judge_stats": judge_stats,
        "triggered": triggered,
        "dim2_records": dim2_records,
        "cost": rpt.get("cost", {}),
        "judges_of_interest": judges_of_interest,
        "ne_samples": [s.get("file") for s in samples if s.get("admission") == "NE"],
    }


def render(a: dict, md: bool = False) -> str:
    L = []
    S = a["summary"]
    L.append("# 双采样真实跑一致性分析（修正口径）\n")
    L.append(f"- 样本数: {a['n_samples']}  | 评估维度对(两次视角): {a['pair_total']}")
    L.append(f"- 配置: model={S.get('model')}  threshold={S.get('threshold')}  mock={S.get('mock')}\n")
    L.append("## 1. 自一致总览")
    L.append(f"- 走 average 路径: {a['n_avg']} / {a['pair_total']}")
    L.append(f"- 触发层内仲裁: {a['n_arbitrated']}（其中真不确定性 arbitrated_ne: {a['n_ne_arb']}）")
    L.append(f"- 两次视角分差=0 (完全一致): {a['n_zero']}")
    L.append(f"- 分差>1 (明显分歧): {a['n_gt1']}")
    L.append(f"- 分差均值: {a['mean_diff']}  | 分差最大值: {a['max_diff']}")
    L.append(f"- 完全失败维度(连仲裁无分): {len(a['hard_fail'])}\n")
    L.append("## 2. 各 Judge 稳定性 (按两次视角分差，仅数值维度对)")
    L.append("| Judge | 维度对数 | 分差均值 | 分差最大 | 完全一致数 |")
    L.append("|---|---|---|---|---|")
    for jk, st in a["judge_stats"].items():
        label = a["judges_of_interest"].get(jk, jk)
        L.append(f"| {label} | {st['n']} | {st['mean']} | {st['max']} | {st['zero']} |")
    L.append("")
    L.append("## 3. 触发层内仲裁的具体维度")
    L.append("| 样本 | Judge | 维度 | a | b | 重算diff | resolution |")
    L.append("|---|---|---|---|---|---|---|")
    for (f, jk, dim, av, bv, d, res) in a["triggered"]:
        L.append(f"| {f} | {jk} | {dim} | {av} | {bv} | {d} | {res} |")
    L.append("")
    L.append("## 4. 完全失败维度（a/b/score 全 None，需排查 Judge 输出）")
    if a["hard_fail"]:
        L.append("| 样本 | Judge | 维度 | resolution |")
        L.append("|---|---|---|---|")
        for (f, jk, dim, res) in a["hard_fail"]:
            L.append(f"| {f} | {jk} | {dim} | {res} |")
    else:
        L.append("- 无")
    L.append("")
    L.append("## 5. ² 修复验证：好样本是否仍被误判 FAIL (维度2 + 准入)")
    L.append("| 样本 | 维度2分 | admission | redline |")
    L.append("|---|---|---|---|")
    for (f, d2, adm, rl) in a["dim2_records"]:
        L.append(f"| {f} | {d2} | {adm} | {rl} |")
    L.append("")
    L.append(f"## 6. 样本级 NE（双采样保守降级）: {a['ne_samples'] or '无'}")
    L.append("- 成因：FactJudge 两次视角(strict_rubric/learner_view)的 G0 准入判定不一致 → 保守定为 NE，")
    L.append("  而非给出可能错误的分数。这恰是非确定性被捕获的证据（见 §3/§4 的不稳定维度）。")
    L.append("")
    L.append("## 7. 成本")
    c = a["cost"]
    L.append(f"- API 调用: {c.get('api_calls')}  | 缓存命中: {c.get('cache_hits')}  | 层内仲裁: {c.get('arbitrations')}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--md", default="")
    args = ap.parse_args()
    rpt = load(args.report)
    a = analyze(rpt)
    out = render(a)
    print(out)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"\n[written] {args.md}")


if __name__ == "__main__":
    main()
