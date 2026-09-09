"""合并多批判别力实验结果（分维度跑时用到）。

为什么需要
----------
修 rubric 或改注入用例后，往往只需重跑受影响的维度，
已达标维度的结果应保留复用（省 API 额度、也避免重跑引入新波动）。
本脚本把多批结果按维度合并为一份完整报告。

用法
----
    python scripts/merge_discrimination.py 结果1.json 结果2.json ... \
        --out results/discrimination.json

冲突处理：后出现的文件覆盖先出现的（同维度同档位）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIMS = ["1", "4", "7", "8", "9"]
LEVELS = ("mild", "severe")

LOCATE_HINT = {
    "1": ["理解", "掌握", "素养", "全面发展"],
    "4": ["教师讲解", "教师板书", "教师演示", "自行把握"],
    "7": ["参差不齐", "因材施教", "基本情况一般"],
    "8": ["表现打分", "登记分数", "考试分数", "不另作调整"],
    "9": ["齐声", "是不是", "对不对", "无需学生独立思考", "记忆并默写",
          "不设提问", "观看演示", "抄写性质条文"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="+", help="待合并的结果 json（后者覆盖前者）")
    ap.add_argument("--out", default="results/discrimination.json")
    ap.add_argument("--min-drop", type=int, default=1)
    args = ap.parse_args()

    base: dict[str, int | None] = {}
    cases: dict[str, dict[str, dict]] = {d: {} for d in DIMS}
    model = "merged"
    for p in args.inputs:
        f = Path(p)
        if not f.is_absolute():
            f = ROOT / f
        d = json.loads(f.read_text(encoding="utf-8"))
        model = d.get("model", model)
        for k, v in (d.get("base") or {}).items():
            base[k] = v
        for dim, lv in (d.get("cases") or {}).items():
            cases.setdefault(dim, {}).update(lv)

    rows, all_ok = [], True
    for dim in DIMS:
        b, m, s = (base.get(dim),
                   cases[dim].get("mild", {}).get("score"),
                   cases[dim].get("severe", {}).get("score"))
        if b is None or m is None or s is None:
            rows.append((dim, b, m, s, "✗ 有维度未出分（NE）"))
            all_ok = False
            continue
        checks = []
        if b - s < args.min_drop:
            checks.append(f"重度未识别(Δ{b - s})")
        if b - m < args.min_drop:
            checks.append(f"轻度未识别(Δ{b - m})")
        if m < s:
            checks.append("倒挂(mild<severe)")
        ok = not checks
        all_ok &= ok
        rows.append((dim, b, m, s, "✓" if ok else "✗ " + "；".join(checks)))

    print(f"模型：{model}\n")
    print(f"{'维度':<6}{'base':<8}{'mild':<8}{'severe':<8}{'判定'}")
    print("-" * 66)
    for dim, b, m, s, verdict in rows:
        f = lambda v: "—" if v is None else str(v)  # noqa: E731
        print(f"D{dim:<5}{f(b):<8}{f(m):<8}{f(s):<8}{verdict}")
    print("-" * 66)

    n = len(rows)
    passed = sum(1 for dim, b, m, s, _ in rows
                 if None not in (b, m, s)
                 and b - s >= args.min_drop and b - m >= args.min_drop and m >= s)
    print(f"顺序正确率（目标 ≥90%）：{passed}/{n} = {passed / n * 100:.0f}%")

    located = total = 0
    for dim in DIMS:
        for lv in LEVELS:
            ev = cases.get(dim, {}).get(lv, {}).get("evidence") or ""
            if cases.get(dim, {}).get(lv):
                total += 1
                if any(k in ev for k in LOCATE_HINT.get(dim, [])):
                    located += 1
    if total:
        print(f"证据定位率（目标 ≥80%）：{located}/{total} = "
              f"{located / total * 100:.0f}%")

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "base": base, "cases": cases},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n合并结果 → {out.relative_to(ROOT)}")
    print("结论：", "✓ 判别力达标" if all_ok else "✗ 仍有维度未达标")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
