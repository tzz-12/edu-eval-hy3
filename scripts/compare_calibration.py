#!/usr/bin/env python3
"""对比两轮校准结果：round1（PDF 抽取）vs round2（清洗后 Markdown）。

关注三件事：
1. 准入变化：补断言后原来 NE 的样本是否被解封（这是扩题的直接收益）；
2. 降噪效果：同一份课例换成清洗文本后，各维度分数是否上升（维度 5 清晰度
   在第一轮有一半扣分来自 PDF 公式抽取噪声）；
3. 分数分布：直方图看 Judge 是否「中间塌缩」（只给 3 分和 5 分、不给 2/4 分）。

用法（仓库根目录）：
  python scripts/compare_calibration.py                 # 默认对比 round1 / round2
  python scripts/compare_calibration.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any, Dict, List, Optional

DIMS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "A"]

SAMPLES = [
    ("quadratic.json", "二次函数图象性质"),
    ("parallelogram.json", "平行四边形性质"),
    ("square.json", "丰富多彩的正方形"),
    ("tessellation.json", "平面镶嵌"),
]


def load(result_dir: str, fn: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(result_dir, fn)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dim_score(r: Dict[str, Any], d: str) -> Optional[int]:
    slot = (r.get("scores") or {}).get(d) or {}
    return slot.get("score")


def fmt(v: Optional[int]) -> str:
    return "NE" if v is None else str(v)


def delta(a: Optional[int], b: Optional[int]) -> str:
    """round1 → round2 的变化。NE→有分记「解封」，有分→NE 记「退化为 NE」。"""
    if a is None and b is None:
        return "—"
    if a is None:
        return f"NE→{b} 解封"
    if b is None:
        return f"{a}→NE 退化"
    return f"{a}→{b} ({b - a:+d})"


def distribution(rows: List[Dict[str, Any]], key: str) -> Counter:
    """统计某一轮里所有样本各维度的分值分布。"""
    c: Counter = Counter()
    for r in rows:
        for d in DIMS:
            v = dim_score(r, d)
            if v is not None:
                c[v] += 1
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description="对比两轮校准结果")
    ap.add_argument("--base", default="results/calibration", help="第一轮结果目录")
    ap.add_argument("--new", default="results/calibration_v2", help="第二轮结果目录")
    ap.add_argument("--json", default="", help="额外输出 JSON")
    args = ap.parse_args()

    print(f"{'样本':<14} {'轮次':<6} {'准入':<6} {'总分':<6} {'等级':<6} "
          + "".join(d.ljust(3) for d in DIMS))
    print("-" * 92)

    base_rows: List[Dict[str, Any]] = []
    new_rows: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}

    for fn, name in SAMPLES:
        r1, r2 = load(args.base, fn), load(args.new, fn)
        if r1 is None and r2 is None:
            print(f"{name:<14} 两轮均无结果，跳过")
            continue
        if r1:
            base_rows.append(r1)
        if r2:
            new_rows.append(r2)

        for tag, r in (("round1", r1), ("round2", r2)):
            if r is None:
                continue
            agg = r.get("aggregation") or {}
            cells = "".join(fmt(dim_score(r, d)).ljust(3) for d in DIMS)
            print(f"{name:<14} {tag:<6} {str(r.get('admission')):<6} "
                  f"{str(agg.get('total_score') or '-'):<6} "
                  f"{str(agg.get('grade') or '-'):<6} {cells}")

        if r1 and r2:
            deltas = [f"维度{d}:{delta(dim_score(r1, d), dim_score(r2, d))}"
                      for d in DIMS
                      if delta(dim_score(r1, d), dim_score(r2, d)) != "—"]
            print(f"{'':<14} 变化   " + " | ".join(deltas))
        print("-" * 92)
        summary[name] = {
            "r1_total": (r1 or {}).get("aggregation", {}).get("total_score"),
            "r2_total": (r2 or {}).get("aggregation", {}).get("total_score"),
            "r1_admission": (r1 or {}).get("admission"),
            "r2_admission": (r2 or {}).get("admission"),
        }

    # ---- 准入解封统计
    print("\n=== 准入变化（扩断言的直接收益）===")
    for fn, name in SAMPLES:
        r1, r2 = load(args.base, fn), load(args.new, fn)
        if not r1 or not r2:
            continue
        a, b = r1.get("admission"), r2.get("admission")
        mark = "✓ 解封" if a == "NE" and b != "NE" else (
            "✗ 仍拒评" if b == "NE" else "— 保持")
        print(f"  {name:<14} {a} → {b}   {mark}")

    # ---- 分数分布（查中间塌缩）
    print("\n=== 维度分值分布（查「中间塌缩」：2/4 分是否缺失）===")
    for tag, rows in (("round1", base_rows), ("round2", new_rows)):
        if not rows:
            continue
        dist = distribution(rows, "score")
        total = sum(dist.values())
        cells = "  ".join(f"{s}分:{dist.get(s, 0)}" for s in range(1, 6))
        print(f"  {tag}（n={total}）：{cells}")
        mid = dist.get(2, 0) + dist.get(4, 0)
        print(f"      中间档（2/4 分）占比：{mid}/{total} = "
              f"{mid / total:.0%}" if total else "      无数据")

    # ---- 各维度均值
    print("\n=== 各维度均值（忽略 NE）===")
    print(f"{'维度':<6}{'round1':<18}{'round2':<18}")
    for d in DIMS:
        out = [d.ljust(6)]
        for rows in (base_rows, new_rows):
            vals = [v for r in rows if (v := dim_score(r, d)) is not None]
            out.append((f"{sum(vals)/len(vals):.2f} (n={len(vals)}, "
                        f"{min(vals)}-{max(vals)})" if vals else "全部 NE").ljust(18))
        print("".join(out))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n摘要已写入：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
