"""新增 1 条 Tier1 条目（位似变换），schema 化改写自 pep-math-taxonomy mt_ch27_004。

许可: pep-math-taxonomy 内容 CC BY-SA 4.0, 衍生条目按相同许可共享
入库约定: 与 K12-KGraph (CC BY-NC-SA 4.0) 不同许可, 通过独立 source 标注,
          标准 schema 校验要求非空 license/source/version 字段。

新增条目 ID: math_9b_rjb_ext001 (延伸条目, 不污染原 cpt 编号)
"""
from __future__ import annotations

import json
from pathlib import Path
from dataclasses import asdict

KB_PATH = Path('data/kb/knowledge.jsonl')

NEW_ENTRY = {
    'id': 'math_9b_rjb_ext001',
    'name': '位似变换',
    'tier': 1,
    'definition': (
        '以某点 O 为位似中心，将图形各点到 O 的距离按相同比例 k 放大或缩小，'
        '得到位似图形。位似图形一定相似，对应点连线交于位似中心。'
        '在坐标系中，以原点为位似中心时对应坐标成比例。'
    ),
    'importance': '理解',
    'grade': '九年级下册',
    'publisher': '人教版',
    'aliases': ['位似图形', '位似中心'],
    'prerequisites': [],  # 留空, 由人工/Phase 1 补
    'related': [],
    'formula': '',  # 公式具体如 y=kx, 留空避免噪声
    'examples': [],
    'source': 'pep-math-taxonomy',  # 衍生整理自 PEP 2022 知识图谱
    'license': 'CC BY-SA 4.0',  # 与原仓库一致, ShareAlike 约束衍生条目
    'version': 'snapshot-2022-entities+2026-09-ext',
    'std_ref': 'pep-math-2022:9下.27.3',  # 27.3 节相似判定
    'quarantined': False,
}


def main():
    entries = []
    if KB_PATH.exists():
        for line in open(KB_PATH):
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    # 幂等: 已存在则不重复添加
    if any(e['id'] == NEW_ENTRY['id'] for e in entries):
        print(f'{NEW_ENTRY["id"]} 已存在, 跳过')
        return

    # 校验必需字段（schema.py 校验规则）
    from edu_eval.kb.schema import Tier1Entry
    e = Tier1Entry.from_dict(NEW_ENTRY)
    errs = e.validate()
    if errs:
        for x in errs:
            print('校验失败:', x)
        raise SystemExit(1)
    print(f'校验通过: {NEW_ENTRY["name"]}')

    entries.append(NEW_ENTRY)
    with open(KB_PATH, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

    print(f'写入 {KB_PATH}: {NEW_ENTRY["id"]} = {NEW_ENTRY["name"]}')
    print(f'知识库现 {len(entries)} 条')


if __name__ == '__main__':
    main()
