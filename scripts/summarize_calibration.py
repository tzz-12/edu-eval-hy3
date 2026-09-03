#!/usr/bin/env python3
"""汇总校准样本各维度分数分布。"""
import json

FILES = [
    ('parallelogram.json', '平行四边形性质'),
    ('square.json', '丰富多彩的正方形'),
    ('tessellation.json', '平面镶嵌'),
    ('quadratic.json', '二次函数图象性质'),
]
DIMS = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'A']

all_scores = {}
for fn, name in FILES:
    r = json.load(open(f'results/calibration/{fn}'))
    adm = r['admission']
    agg = r['aggregation']
    print(f"== {name}")
    print(f"   admission={adm} | redline={r['redline']} | verdict={agg.get('verdict')} | total={agg.get('total_score')} | grade={agg.get('grade')}")
    dims = {}
    for dk, dv in sorted(r['scores'].items(), key=lambda x: (len(x[0]), x[0])):
        sc = dv.get('score')
        dims[dk] = sc
        print(f"   维度{dk}: {'NE' if sc is None else sc}")
    all_scores[name] = dims
    w = r.get('warnings', [])
    if w:
        print(f"   warnings({len(w)}):", '; '.join(str(x)[:60] for x in w[:3]))

print()
print("=== 维度分汇总（5分制）===")
print('样本'.ljust(16) + ''.join(d.ljust(4) for d in DIMS))
for name, dims in all_scores.items():
    row = name.ljust(16)
    for d in DIMS:
        v = dims.get(d)
        row += ('NE' if v is None else str(v)).ljust(4)
    print(row)

# 各维度均值（忽略 NE）
print()
print("=== 各维度均值 ===")
for d in DIMS:
    vals = [dims.get(d) for dims in all_scores.values() if dims.get(d) is not None]
    if vals:
        print(f"维度{d}: 均值 {sum(vals)/len(vals):.2f} (n={len(vals)}, min={min(vals)}, max={max(vals)})")
    else:
        print(f"维度{d}: 全部 NE")
