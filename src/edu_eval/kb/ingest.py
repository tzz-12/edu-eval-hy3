"""K12-KGraph → Tier 1 知识库导入。

从 data/raw/k12_math.json 导入初中 451 个 Concept 为 Tier1Entry：
- 写出 data/kb/knowledge.jsonl（人读、可 diff）
- 建 data/kb/knowledge.db（SQLite FTS5 索引，供 retriever.py 检索）

年级推导走图谱边（不依赖 id 命名约定）：
  Concept --appears_in--> Section --is_part_of--> Chapter --is_part_of--> Book
导入完成后校验边推导结果与 id 约定（math_{7a..9b}_rjb_*）的一致性，
不一致的概念标记 quarantined，不参与检索与 G0。

用法：python -m edu_eval.kb.ingest  （在 src/ 或仓库根目录下）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections import Counter
from typing import Dict, List, Optional

from edu_eval.kb.schema import Tier1Entry, dump_jsonl

BOOK_GRADE = {
    "math_7a_rjb": "七年级上册",
    "math_7b_rjb": "七年级下册",
    "math_8a_rjb": "八年级上册",
    "math_8b_rjb": "八年级下册",
    "math_9a_rjb": "九年级上册",
    "math_9b_rjb": "九年级下册",
}
JUNIOR_ID_RE = re.compile(r"^math_(7a|7b|8a|8b|9a|9b)_rjb_")

SOURCE = "k12-kgraph"
LICENSE = "CC BY-NC-SA 4.0"
VERSION = "snapshot-2025"


def _iter_edges(edges: List[dict]):
    """归一化 K12-KGraph 的 4 种边结构，产出 (type, source, target)。"""
    for e in edges:
        if "target" in e:
            yield e["type"], e["source"], e["target"]
        for t in e.get("target_name_to_ids", []):
            yield e["type"], e["source"], t["target"]


def _parents(edges: List[dict]) -> Dict[str, str]:
    """is_part_of 父节点映射：子节点 id → 父节点 id（Section→Chapter、Chapter→Book）。"""
    return {p: t for ty, p, t in _iter_edges(edges) if ty == "is_part_of"}


def _book_of(node_id: str, parent: Dict[str, str]) -> Optional[str]:
    cur = node_id
    for _ in range(3):  # Section → Chapter → Book 至多两跳
        cur = parent.get(cur)
        if cur is None:
            return None
        if cur in BOOK_GRADE:
            return cur
    return None


def _grade_from_id(node_id: str) -> Optional[str]:
    m = JUNIOR_ID_RE.match(node_id)
    return BOOK_GRADE[f"math_{m.group(1)}_rjb"] if m else None


def load_concepts(raw_path: str):
    """读原始图谱，返回 (tier1 条目列表, 统计信息)。"""
    with open(raw_path, encoding="utf-8") as f:
        g = json.load(f)
    nodes, edges = g["nodes"], g["edges"]
    node_by_id = {n["id"]: n for n in nodes}
    parent = _parents(edges)

    appears_in: Dict[str, set] = {}
    prereq_of: Dict[str, List[str]] = {}
    related: Dict[str, List[str]] = {}
    for ty, s, t in _iter_edges(edges):
        if ty == "appears_in":
            appears_in.setdefault(s, set()).add(t)
        elif ty == "prerequisites_for":
            prereq_of.setdefault(t, []).append(s)  # s 是 t 的前置
        elif ty == "relates_to":
            related.setdefault(s, []).append(t)
            related.setdefault(t, []).append(s)

    entries: List[Tier1Entry] = []
    stats = Counter()
    # K12-KGraph 存在脏别名（如「频数分布直方图」被逐字拆为 7 个单字），
    # 单字别名无区分度且引发误召回，导入时一律剔除
    def _clean_aliases(raw) -> List[str]:
        return [a for a in (raw or []) if len(a) >= 2]
    for n in nodes:
        if n["label"] != "Concept" or not JUNIOR_ID_RE.match(n["id"]):
            continue
        props = n.get("properties", {})
        # 年级：沿边推导（可跨册复现，取集合）
        sec_books = {_book_of(sec, parent) for sec in appears_in.get(n["id"], ())}
        grades = {BOOK_GRADE[b] for b in sec_books if b}
        grade = "、".join(sorted(grades)) if grades else ""
        grade_id = _grade_from_id(n["id"])

        # 边推导 vs id 约定 一致性校验：id 年级（首次定义处）须包含于边推导集合；
        # 跨册复现（如幂的运算：七上+八上）是图谱真实特性，不算不一致
        mismatch = bool(grades) and grade_id is not None and grade_id not in grades
        if mismatch:
            stats["grade_mismatch_quarantined"] += 1
        elif len(grades) > 1:
            stats["grade_multi_book"] += 1
        elif grades:
            stats["grade_from_edges"] += 1
        else:
            stats["grade_from_id_fallback"] += 1

        entries.append(Tier1Entry(
            id=n["id"],
            name=n["name"],
            definition=props.get("definition", ""),
            importance=props.get("importance", ""),
            grade=grade or grade_id or "",
            publisher="人教版",
            aliases=_clean_aliases(props.get("aliases")),
            prerequisites=sorted(set(prereq_of.get(n["id"], []))),
            related=sorted(set(related.get(n["id"], []))),
            formula=props.get("formula", ""),
            examples=list(props.get("examples") or []),
            source=SOURCE,
            license=LICENSE,
            version=VERSION,
            quarantined=bool(mismatch),
        ))
    stats["total_concepts"] = len(entries)
    return entries, stats


def build_fts(entries: List[Tier1Entry], db_path: str) -> None:
    """SQLite FTS5 索引。中文按字切分（空格分隔），支持 1–2 字查询。"""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE VIRTUAL TABLE kb_fts USING fts5("
        "id UNINDEXED, grade UNINDEXED, quarantined UNINDEXED,"
        "name, aliases, definition, tokenize='unicode61')"
    )
    rows = []
    for e in entries:
        if e.quarantined:
            continue
        rows.append((
            e.id, e.grade, 0,
            _space(e.name), _space(" ".join(e.aliases)), _space(e.definition),
        ))
    con.executemany("INSERT INTO kb_fts VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _space(text: str) -> str:
    """中文按字切分：'勾股定理' → '勾 股 定 理'。"""
    return " ".join(text) if text else ""


def _append_extensions(entries: List[Tier1Entry], ext_path: str) -> int:
    """重建知识库后追加 extensions.jsonl 中的人工延伸条目 (P1-3)。

    约定：extensions.jsonl 中条目走 schema.Tier1Entry, ID 后缀 _extNNN,
    用于消化不在 K12-KGraph 中但教学评测需要的细粒度概念
    （如：位似变换 来源 pep-math-taxonomy CC BY-SA 4.0）。
    """
    if not os.path.exists(ext_path):
        return 0
    ext_ids = {e.id for e in entries}
    appended = 0
    with open(ext_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if d.get('id') in ext_ids:
                continue  # 避免与基础集冲突
            e = Tier1Entry.from_dict(d)
            errs = e.validate()
            if errs:
                print(f"[跳过延伸条目校验失败] {d.get('id')}: {errs}", file=sys.stderr)
                continue
            entries.append(e)
            ext_ids.add(e.id)
            appended += 1
    return appended


def _restore_std_ref(entries: List[Tier1Entry], jsonl_path: str) -> int:
    """重建知识库前保留已有 std_ref 字段 (P1-3 · 防止 backfill 被覆盖)。

    来源: src/edu_eval/kb/backfill_std_ref.py
    """
    if not os.path.exists(jsonl_path):
        return 0
    preserved: Dict[str, str] = {}
    with open(jsonl_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            sr = d.get('std_ref')
            if sr and d.get('id'):
                preserved[d['id']] = sr
    restored = 0
    for e in entries:
        if e.id in preserved and not e.std_ref:
            e.std_ref = preserved[e.id]
            restored += 1
    return restored


def main() -> None:
    ap = argparse.ArgumentParser(description="导入 K12-KGraph 为 Tier 1 知识库")
    ap.add_argument("--raw", default="data/raw/k12_math.json")
    ap.add_argument("--out-jsonl", default="data/kb/knowledge.jsonl")
    ap.add_argument("--out-db", default="data/kb/knowledge.db")
    ap.add_argument("--ext", default="data/kb/extensions.jsonl", help="人工延伸条目源")
    args = ap.parse_args()

    entries, stats = load_concepts(args.raw)

    problems = {e.id: errs for e in entries if (errs := e.validate())}
    if problems:
        for eid, errs in list(problems.items())[:10]:
            print(f"[校验失败] {eid}: {errs}", file=sys.stderr)
        raise SystemExit(f"共 {len(problems)} 条 Tier1 条目未通过校验，中止")

    restored = _restore_std_ref(entries, args.out_jsonl)
    if restored:
        print(f"保留 std_ref: {restored} 条 (来源 backfill_std_ref)")

    dump_jsonl(entries, args.out_jsonl)

    appended = _append_extensions(entries, args.ext)
    if appended:
        print(f"追加延伸条目: {appended} 条 (来源 {args.ext})")
        dump_jsonl(entries, args.out_jsonl)

    build_fts(entries, args.out_db)
    build_fts(entries, args.out_db)

    alias_filled = sum(1 for e in entries if e.aliases)
    formula_filled = sum(1 for e in entries if e.formula)
    print(f"Tier1 条目：{stats['total_concepts']} → {args.out_jsonl}")
    print(f"FTS5 索引：{stats['total_concepts'] - stats['grade_mismatch_quarantined']} 条 → {args.out_db}")
    print(f"年级推导：边={stats['grade_from_edges']} id兜底={stats['grade_from_id_fallback']} "
          f"不一致隔离={stats['grade_mismatch_quarantined']}")
    print(f"aliases 填充：{alias_filled}/{stats['total_concepts']}"
          f" ({alias_filled / stats['total_concepts'] * 100:.1f}%)")
    print(f"formula  填充：{formula_filled}/{stats['total_concepts']}"
          f" ({formula_filled / stats['total_concepts'] * 100:.1f}%)")


if __name__ == "__main__":
    main()
