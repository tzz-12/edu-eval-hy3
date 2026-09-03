"""从 pep-math-taxonomy 主题图谱回填 KB 条目的 std_ref 字段。

数据依据: /tmp/pep-math-taxonomy/data/{7-up, 7-down, ...}/topics.json
关联策略 (优先级降序):
  R1: KB.name 是 topic.name 子串 (KB 更具体, 1:n)
  R2: KB.name 出现在 topic.description 中 (KB 是 topic 内含要素)
  R3: KB.name 包含 topic.name (KB 是大筐, topic 是其细类)

按规则 R1-R2 给 KB.std_ref 赋 topic.standards[0]。
未命中任何规则的 256 条保持 std_ref 空缺 (KB 拆得比 topic 细, 属合理)。

许可处理: ODbL 1.0 (DB) + CC BY-SA 4.0 (内容)
仅复制 std_id 与 std 描述摘要, 不复制原文段落。
回填字段: std_ref 仅, 不动 KB 其它字段。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

REPO = Path('/tmp/pep-math-taxonomy')
SOURCES = [
    ('data/topics.json', '7-up'),
    ('data/7-down/topics.json', '7-down'),
    ('data/8-up/topics.json', '8-up'),
    ('data/8-down/topics.json', '8-down'),
    ('data/9-up/topics.json', '9-up'),
    ('data/9-down/topics.json', '9-down'),
]


def load_topics():
    """载入 169 个 topic, 每条带 standards 列表."""
    out = []
    for rel, bk in SOURCES:
        d = json.load(open(REPO / rel))
        for t in d.get('topics') or d:
            t['_book'] = bk
            out.append(t)
    return out


def find_topic_for_kb(kb_name: str, topics: list) -> Optional[Tuple[dict, str]]:
    """对 KB.name 找最匹配 topic 与命中规则."""
    # R1: KB.name in topic.name (KB 更具体, 短 topic 优先=更精确)
    cands = [(t, 'R1') for t in topics if kb_name in t.get('name', '')]
    if cands:
        cands.sort(key=lambda x: len(x[0].get('name', '')))
        return cands[0]

    # R2: topic.description 含 kb_name (要求 ≥2 字避免噪声)
    if len(kb_name) >= 2:
        cands = [(t, 'R2') for t in topics if kb_name in t.get('description', '')]
        if cands:
            cands.sort(key=lambda x: len(x[0].get('description', '')))
            return cands[0]

    # R3: topic.name in KB.name (KB 是大筐)
    cands = [(t, 'R3') for t in topics
             if len(t.get('name', '')) >= 3 and t.get('name', '') in kb_name]
    if cands:
        # 取最长 topic 名 (更具体的细分)
        cands.sort(key=lambda x: -len(x[0].get('name', '')))
        return cands[0]

    return None


def backfill(kb_jsonl: Path, topics: list) -> dict:
    """回填 std_ref 字段到 KB jsonl. 返回统计."""
    entries = [json.loads(l) for l in open(kb_jsonl) if l.strip()]
    n_filled = 0
    n_skipped = 0
    rule_dist = {'R1': 0, 'R2': 0, 'R3': 0, 'skipped': 0}

    for e in entries:
        if e.get('std_ref'):
            # 已填, 跳过 (幂等)
            n_skipped += 1
            rule_dist['skipped'] += 1
            continue
        r = find_topic_for_kb(e['name'], topics)
        if r is None:
            n_skipped += 1
            continue
        t, rule = r
        stds = t.get('standards') or []
        if not stds:
            n_skipped += 1
            continue
        # 填第一个 std_ref (KB 一条对一个 std)
        e['std_ref'] = stds[0]
        rule_dist[rule] += 1
        n_filled += 1

    # 写回
    with open(kb_jsonl, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')

    return {
        'total': len(entries),
        'filled': n_filled,
        'skipped': n_skipped,
        'by_rule': rule_dist,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--kb', default='data/kb/knowledge.jsonl', help='Tier1 KB jsonl path')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    topics = load_topics()
    print(f'载入 topics: {len(topics)}')

    if args.dry_run:
        # 试演
        entries = [json.loads(l) for l in open(args.kb) if l.strip()]
        n_filled = 0
        for e in entries:
            if not e.get('std_ref'):
                r = find_topic_for_kb(e['name'], topics)
                if r and r[0].get('standards'):
                    n_filled += 1
        print(f'预计回填: {n_filled}/{len(entries)} = {n_filled*100//len(entries)}%')
        return

    stats = backfill(Path(args.kb), topics)
    print(f'KB 总数: {stats["total"]}')
    print(f'回填条数: {stats["filled"]}')
    print(f'未回填: {stats["skipped"]}')
    print(f'按规则: {stats["by_rule"]}')


if __name__ == '__main__':
    main()
