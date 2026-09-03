"""P1-1：用 Hy3 生成 13 课题的 Tier 2 可核验断言集，经确定性校验后落盘。

流程（每个课题）：
  1. 生成：Hy3 输出候选断言（严格 JSON）
  2. 硬校验：sympy 能算的真算（恒等式/求根/数值采样）→ verified / quarantined
  3. 一致性复核：算不了的结构性断言 → 独立复核提示重新推导证伪
  4. 落盘：data/assertions/tier2_topic_XX.jsonl + 统计摘要

用法（仓库根目录，需先配置 .env）：
  set -a; source .env; set +a
  PYTHONPATH=src python scripts/build_tier2_assertions.py --topic 3        # 单课题试跑
  PYTHONPATH=src python scripts/build_tier2_assertions.py --all            # 全部 13 课题
  PYTHONPATH=src python scripts/build_tier2_assertions.py --all --no-consensus  # 跳过复核
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from edu_eval.config import Hy3Config                      # noqa: E402
from edu_eval.hy3 import Hy3Client                         # noqa: E402
from edu_eval.kb import assertions as A                    # noqa: E402


def generate(client: Hy3Client, topic: Dict[str, Any],
             attempts: int = 3) -> List[dict]:
    """生成候选断言，解析为空时自动重试。

    实测：hy3 的思维链常吃掉 6500+/8192 token，推理稍长就会把 JSON 截断，
    导致整课题生成失败（13 课题中曾有 5 个因此失败）。截断是**偶发**的，
    重试即可恢复，故不视为课题本身的问题。
    """
    cands: List[dict] = []
    for i in range(attempts):
        raw = client.judge(A.GEN_SYSTEM, A.gen_user_prompt(topic),
                           temperature=0.2)
        cands = A.parse_candidates(raw)
        if cands:
            return cands
        print(f"  · 第 {i+1} 次生成未解析出断言（可能输出被截断），重试…",
              file=sys.stderr)
    # 降级：小批量重试。内容量大的课题（如一元二次方程）思维链会稳定超预算，
    # 与其反复失败，不如少生成几条——断言集宁缺勿滥，错了更糟。
    print("  · 全量生成持续失败，降级为小批量（6 条）重试…", file=sys.stderr)
    for i in range(2):
        raw = client.judge(A.GEN_SYSTEM, A.gen_user_prompt(topic, max_items=6),
                           temperature=0.2)
        cands = A.parse_candidates(raw)
        if cands:
            return cands
        print(f"  · 小批量第 {i+1} 次仍失败，重试…", file=sys.stderr)
    return cands


def consensus(client: Hy3Client, topic: Dict[str, Any],
              cands: List[dict]) -> Dict[int, tuple]:
    """一致性复核：分批（≤6 条/批）+ 覆盖率不足自动重试一次。

    实测：一次性复核 10 条时模型偶发漏返回部分 idx；缺结论的条目一律
    记为 unverified（NE），但覆盖率过低会白白损失断言，故整批重试一次。
    """
    if not cands:
        return {}
    batch = 6
    out: Dict[int, tuple] = {}
    for start in range(0, len(cands), batch):
        chunk = cands[start:start + batch]
        verdicts: Dict[int, tuple] = {}
        for attempt in range(2):
            raw = client.judge(A.CONSENSUS_SYSTEM,
                               A.consensus_user_prompt(topic, chunk),
                               temperature=0.0)
            verdicts = A.parse_verdicts(raw, n_items=len(chunk))
            if len(verdicts) >= max(1, len(chunk) * 0.6):
                break
        for k, v in verdicts.items():
            out[start + k] = v
    return out


def build_topic(client: Hy3Client, topic: Dict[str, Any],
                use_consensus: bool = True) -> Dict[str, Any]:
    cands = generate(client, topic)
    if not cands:
        return {"topic": topic["name"], "no": topic["no"], "total": 0,
                "verified": 0, "unverified": 0, "quarantined": 0,
                "entries": [], "error": "生成失败：未解析出任何候选断言"}

    pending: List[int] = []
    results: List[tuple] = []
    for c in cands:
        method, status, detail = A.verify_candidate(c)
        if status == "unverified" and use_consensus:
            pending.append(len(results))
        results.append((method, status, detail))

    if pending:
        need = [cands[i] for i in pending]
        verdicts = consensus(client, topic, need)
        for slot, idx in enumerate(pending):
            results[idx] = A.apply_consensus(cands[idx], verdicts.get(slot))

    entries: List[dict] = []
    stats = {"verified": 0, "unverified": 0, "quarantined": 0}
    for seq, (c, (method, status, detail)) in enumerate(zip(cands, results), 1):
        entry = A.make_entry(c, topic, seq, method, status, detail)
        errs = A.validate_entry(entry)
        if errs:
            print(f"  ! 条目 {entry['id']} schema 校验失败，跳过：{errs[:2]}",
                  file=sys.stderr)
            continue
        stats[status] = stats.get(status, 0) + 1
        entries.append(entry)

    return {"topic": topic["name"], "no": topic["no"], "total": len(entries),
            **stats, "entries": entries}


def write_topic(res: Dict[str, Any], out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"tier2_topic_{res['no']:02d}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for e in res["entries"]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def scan_summary(out_dir: str) -> List[Dict[str, Any]]:
    """扫描已落盘的 tier2_topic_*.jsonl，重算统计摘要（不调用 LLM）。

    状态一律以 `verification.status` 为准：文件顶层的 `quarantined` 字段在
    make_entry 中恒为 False，真正的准入门槛是 `Tier2Entry.is_verified`
    （= 非 quarantined 且 status == verified），两者不可混用。
    """
    summary: List[Dict[str, Any]] = []
    for t in A.TOPICS:
        path = os.path.join(out_dir, f"tier2_topic_{t['no']:02d}.jsonl")
        stats = {"verified": 0, "unverified": 0, "quarantined": 0}
        n = 0
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    n += 1
                    st = (json.loads(line).get("verification") or {}).get(
                        "status", "unverified")
                    stats[st] = stats.get(st, 0) + 1
        else:
            stats["error"] = "文件缺失"
        summary.append({"topic": t["name"], "no": t["no"], "total": n,
                        **stats})
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="构建 Tier 2 可核验断言集")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--topic", type=int,
                   help=f"课题编号 1–{len(A.TOPICS)}")
    g.add_argument("--all", action="store_true",
                   help=f"全部 {len(A.TOPICS)} 课题")
    g.add_argument("--list", action="store_true", help="列出课题清单后退出")
    g.add_argument("--summary-only", action="store_true",
                   help="不调用 LLM，直接扫描 --out 下已落盘的断言重算摘要")
    p.add_argument("--out", default=os.path.join("data", "assertions"))
    p.add_argument("--no-consensus", action="store_true",
                   help="跳过结构性断言的一致性复核")
    p.add_argument("--summary", default="", help="统计摘要写入路径（JSON）")
    args = p.parse_args(argv)

    if args.list:
        for t in A.TOPICS:
            print(f"{t['no']:>3}  {t['name']}（{t['grade']}）"
                  f"{'／' + t['kind'] if t.get('kind') else ''}")
        return 0

    if args.summary_only:
        # 摘要是**派生数据**，只靠 --all 顺带写会漂移：单课题重跑（如补 14–16）
        # 不会更新它，实测一度出现「文件里有断言、摘要却写着生成失败」。
        # 扫盘重算不烧 token，随时可跑。
        summary = scan_summary(args.out)
        tot_v = sum(s.get("verified", 0) for s in summary)
        tot_t = sum(s.get("total", 0) for s in summary)
        tot_q = sum(s.get("quarantined", 0) for s in summary)
        print(f"扫盘重算：{tot_t} 条断言，verified {tot_v}，quarantined {tot_q}")
        for s in summary:
            print(f"  {s['no']:>3} {s['topic']:<16} {s['total']:>3} 条"
                  f"（verified {s['verified']} / quarantined {s['quarantined']}"
                  f" / unverified {s['unverified']}）")
        if args.summary:
            with open(args.summary, "w", encoding="utf-8") as f:
                json.dump({"topics": summary, "total": tot_t,
                           "verified": tot_v, "quarantined": tot_q},
                          f, ensure_ascii=False, indent=2)
            print(f"摘要已写入：{args.summary}")
        return 0

    cfg = Hy3Config.from_env()
    client = Hy3Client(cfg)
    topics = A.TOPICS if args.all else [A.topic_by_no(args.topic)]

    summary: List[Dict[str, Any]] = []
    n_all = len(A.TOPICS)
    for topic in topics:
        print(f"[{topic['no']:02d}/{n_all}] {topic['name']}"
              f"（{topic['grade']}）…", flush=True)
        res = build_topic(client, topic, use_consensus=not args.no_consensus)
        if res.get("error"):
            print(f"  ✗ {res['error']}", file=sys.stderr)
            summary.append(res)
            continue
        path = write_topic(res, args.out)
        tot = max(res["total"], 1)
        print(f"  ✓ {res['total']} 条 → {path}｜verified {res['verified']}"
              f"（{res['verified']/tot:.0%}）unverified {res['unverified']}"
              f" quarantined {res['quarantined']}", flush=True)
        summary.append({k: v for k, v in res.items() if k != "entries"})

    tot_v = sum(s.get("verified", 0) for s in summary)
    tot_t = sum(s.get("total", 0) for s in summary)
    tot_q = sum(s.get("quarantined", 0) for s in summary)
    print(f"\n合计：{tot_t} 条断言，verified {tot_v}（{tot_v/max(tot_t,1):.0%}），"
          f"quarantined {tot_q}")

    if args.summary:
        with open(args.summary, "w", encoding="utf-8") as f:
            json.dump({"topics": summary, "total": tot_t,
                       "verified": tot_v, "quarantined": tot_q},
                      f, ensure_ascii=False, indent=2)
        print(f"摘要已写入：{args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
