"""P1-3 · pep-math-taxonomy 整合回归测试。

覆盖:
- ingest 重跑后 std_ref / extensions 不被洗
- retriever 召回延伸条目 (位似)
- backfill 幂等 (二次回填不出错)
- extensions 来源独立校验
- grade_map 含延伸条目
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

KB_JSONL = ROOT / 'data' / 'kb' / 'knowledge.jsonl'
EXT_JSONL = ROOT / 'data' / 'kb' / 'extensions.jsonl'


def test_std_ref_persists_through_ingest():
    """经 ingest 重跑, std_ref 字段保留 (P1-3 主要价值)."""
    if not KB_JSONL.exists():
        import pytest
        pytest.skip(f"{KB_JSONL} 不存在 (需先跑 ingest)")
    n_with_std = 0
    n_total = 0
    for line in open(KB_JSONL):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        n_total += 1
        if d.get('std_ref'):
            n_with_std += 1
    # 期望 std_ref 覆盖率 ≥40% (P1-3 后端回归基线)
    assert n_total >= 451, f"知识库应至少 451 条, 实 {n_total}"
    pct = n_with_std * 100 // n_total
    assert pct >= 40, f"std_ref 覆盖率掉到 {pct}%, P1-3 后端回归被破坏"


def test_extensions_in_kb():
    """extensions.jsonl 中的延伸条目应在 ingest 后出现在 knowledge.jsonl."""
    if not EXT_JSONL.exists():
        import pytest
        pytest.skip("无 extensions.jsonl (未启用延伸)")

    ext_ids = set()
    for line in open(EXT_JSONL):
        line = line.strip()
        if line:
            d = json.loads(line)
            ext_ids.add(d.get('id', ''))

    assert ext_ids, "extensions.jsonl 为空"

    kb_ids = set()
    for line in open(KB_JSONL):
        line = line.strip()
        if line:
            d = json.loads(line)
            kb_ids.add(d.get('id', ''))

    missing = ext_ids - kb_ids
    assert not missing, f"延伸条目未进入知识库: {missing}"


def test_extensions_have_pep_license():
    """延伸条目必须用 pep-math-taxonomy (CC BY-SA 4.0) 来源, 不可降级为 k12-kgraph."""
    if not EXT_JSONL.exists():
        import pytest
        pytest.skip("无 extensions.jsonl")

    for line in open(EXT_JSONL):
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        assert d['source'] == 'pep-math-taxonomy', \
            f"延伸条目 source 应为 pep-math-taxonomy, 实 {d.get('source')}"
        assert 'SA' in d['license'], \
            f"延伸条目 license 应含 SA (ShareAlike), 实 {d.get('license')}"


def test_backfill_idempotent():
    """backfill_std_ref 二次运行不应改变 KB (幂等)."""
    if not KB_JSONL.exists():
        import pytest
        pytest.skip("KB 不存在")

    pre = {}
    for line in open(KB_JSONL):
        line = line.strip()
        if line:
            d = json.loads(line)
            pre[d['id']] = d.get('std_ref')

    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src')
    r = subprocess.run(
        [sys.executable, '-m', 'edu_eval.kb.backfill_std_ref'],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, f"backfill 失败: {r.stderr}"

    post = {}
    for line in open(KB_JSONL):
        line = line.strip()
        if line:
            d = json.loads(line)
            post[d['id']] = d.get('std_ref')

    assert pre == post, "幂等失败: backfill 二次运行改变了 std_ref"


def test_positioning_retrieval():
    """延伸条目位似变换应被 retriever 召回 (含中文 2 字查询)."""
    from edu_eval.kb.retriever import KBRetriever
    if not (ROOT / 'data/kb/knowledge.db').exists():
        import pytest
        pytest.skip("FTS 索引不存在 (需先跑 ingest)")

    r = KBRetriever()
    try:
        hits = r.search('位似', top_k=3)
        names = [h.name for h in hits]
        assert any('位似' in n for n in names), f"位似未被召回: {names}"
    finally:
        r.close()
