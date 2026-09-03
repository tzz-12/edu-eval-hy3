#!/usr/bin/env python3
"""查看二次函数样本低分维度的证据与建议。"""
import json

r = json.load(open('results/calibration/quadratic.json'))
print("== 维度分数与证据摘要 ==")
for dk, dv in sorted(r['scores'].items(), key=lambda x: (len(x[0]), x[0])):
    sc = dv.get('score')
    ev = (dv.get('evidence') or '')[:180]
    print(f"\n[维度{dk}] {sc}分")
    print("  证据:", ev.replace('\n', ' '))

print("\n== 建议 ==")
for s in r.get('suggestions', [])[:8]:
    print("-", str(s)[:120])
