"""定向补录断言：只追加，不重建。

为什么需要它
------------
`build_tier2_assertions.py --topic N` 会**整文件覆盖** tier2_topic_NN.jsonl。
校准发现断言集缺的是个别定理（如二次函数的图象平移规律、平行四边形的不稳定性），
而同课题其余断言是好的，重跑会白白烧 token 且有概率生成得更差。

因此这里只做「增量补录」，并且**复用完全相同的校验流水线**
（sympy 硬核验 → 无公式走一致性复核 → 不通过一律隔离），
保证补进去的条目与自动生成的条目同标准。

用法（仓库根目录，需先配置 .env）：
  PYTHONPATH=src python scripts/patch_assertions.py patches.json
  PYTHONPATH=src python scripts/patch_assertions.py patches.json --dry-run
  PYTHONPATH=src python scripts/patch_assertions.py patches.json --no-consensus

补丁文件格式（JSON 数组）：
  [{
    "topic_no": 11,
    "assertion": "抛物线 y=a(x-h)²+k 的顶点坐标为 (h, k)",
    "kind": "structural",          # identity / solve / numeric / structural
    "formula": "",                 # 无公式填空串
    "solutions": [],               # 仅 kind=solve
    "conditions": "a ≠ 0",
    "common_errors": ["顶点写成 (-h, k)"],
    "concepts": ["二次函数"],
    "std_ref": ""
  }]
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


def next_seq(path: str) -> int:
    """已有文件的最大序号 +1（决定新条目的 as-XX-NNN 编号）。"""
    if not os.path.exists(path):
        return 1
    mx = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            eid = json.loads(line).get("id", "")
            try:
                mx = max(mx, int(eid.rsplit("-", 1)[-1]))
            except (ValueError, IndexError):
                continue
    return mx + 1


def already_present(path: str, assertion: str) -> bool:
    """同一课题已有语义等价断言时跳过，避免重复补录。"""
    if not os.path.exists(path):
        return False
    key = assertion.strip()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and json.loads(line).get("assertion", "").strip() == key:
                return True
    return False


def patch(client: Hy3Client, topic_no: int, cands: List[Dict[str, Any]],
          out_dir: str, use_consensus: bool = True,
          dry_run: bool = False) -> Dict[str, Any]:
    topic = A.topic_by_no(topic_no)
    path = os.path.join(out_dir, f"tier2_topic_{topic_no:02d}.jsonl")
    seq = next_seq(path)

    pending: List[int] = []
    results: List[tuple] = []
    for c in cands:
        method, status, detail = A.verify_candidate(c)
        if status == "unverified" and use_consensus:
            pending.append(len(results))
        results.append((method, status, detail))

    if pending and use_consensus:
        need = [cands[i] for i in pending]
        raw = client.judge(A.CONSENSUS_SYSTEM,
                           A.consensus_user_prompt(topic, need),
                           temperature=0.0)
        verdicts = A.parse_verdicts(raw, n_items=len(need))
        for slot, idx in enumerate(pending):
            results[idx] = A.apply_consensus(cands[idx], verdicts.get(slot))

    entries: List[dict] = []
    stats = {"verified": 0, "unverified": 0, "quarantined": 0, "skipped": 0}
    for c, (method, status, detail) in zip(cands, results):
        if already_present(path, c.get("assertion", "")):
            print(f"  · 已存在，跳过：{c.get('assertion', '')[:40]}")
            stats["skipped"] += 1
            continue
        entry = A.make_entry(c, topic, seq, method, status, detail)
        errs = A.validate_entry(entry)
        if errs:
            print(f"  ! schema 校验失败，跳过：{errs[:2]}", file=sys.stderr)
            stats["skipped"] += 1
            continue
        stats[status] = stats.get(status, 0) + 1
        entries.append(entry)
        seq += 1
        flag = {"verified": "✓", "unverified": "?", "quarantined": "✗"}[status]
        print(f"  {flag} {entry['id']} [{method}] {entry['assertion'][:56]}")

    if entries and not dry_run:
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        print(f"  → 追加 {len(entries)} 条到 {path}")
    elif dry_run:
        print(f"  → dry-run：{len(entries)} 条待写入 {path}")
    return {"topic": topic["name"], "no": topic_no, "added": len(entries),
            **stats}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="定向补录 Tier 2 断言")
    p.add_argument("patch_file", help="补丁 JSON 文件路径")
    p.add_argument("--out", default=os.path.join("data", "assertions"))
    p.add_argument("--no-consensus", action="store_true",
                   help="跳过结构性断言的一致性复核（离线校验用）")
    p.add_argument("--dry-run", action="store_true", help="只校验不落盘")
    args = p.parse_args(argv)

    with open(args.patch_file, encoding="utf-8") as f:
        data = json.load(f)

    by_topic: Dict[int, List[Dict[str, Any]]] = {}
    for item in data:
        by_topic.setdefault(int(item["topic_no"]), []).append(item)

    client = None
    if not args.no_consensus:
        client = Hy3Client(Hy3Config.from_env())

    print(f"补丁：{args.patch_file}（{len(data)} 条 / {len(by_topic)} 个课题）")
    for topic_no in sorted(by_topic):
        print(f"\n[{A.topic_by_no(topic_no)['name']}]")
        res = patch(client, topic_no, by_topic[topic_no], args.out,
                    use_consensus=not args.no_consensus, dry_run=args.dry_run)
        print(f"  小计：新增 {res['added']}，verified {res.get('verified', 0)}，"
              f"quarantined {res.get('quarantined', 0)}，"
              f"unverified {res.get('unverified', 0)}，"
              f"跳过 {res.get('skipped', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
