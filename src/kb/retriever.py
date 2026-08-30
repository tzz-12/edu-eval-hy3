"""Tier 1 知识库检索器（SQLite FTS5）。

检索策略（分层合并）：
1. 名称/别名精确匹配        → 最高置信
2. 名称/别名 LIKE 包含     → 高置信（如查询「二次函数的图象」命中「二次函数」）
3. FTS 逐字全文匹配        → 兜底（定义文本召回）

中文按字切分建索引，1–2 字查询（如「分式」）也能召回。
quarantined 条目在建库时已排除。
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Hit:
    id: str
    name: str
    grade: str
    definition: str
    score: float
    match_type: str  # exact / contains / fts


def _space(text: str) -> str:
    return " ".join(text) if text else ""


class KBRetriever:
    def __init__(self, db_path: str = "data/kb/knowledge.db",
                 jsonl_path: Optional[str] = None):
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"知识库索引 {db_path} 不存在，请先运行: python src/kb/ingest.py"
            )
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        # 名称/别名精确与包含匹配用附属表（含 FTS 之外的原始字段）
        self._load_names(jsonl_path or os.path.join(
            os.path.dirname(db_path), "knowledge.jsonl"))
        self._fts_space = _space  # 保留引用

    def _load_names(self, jsonl_path: str) -> None:
        import json
        self._names: List[dict] = []
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("quarantined"):
                    continue
                self._names.append(obj)

    # ---------- 检索 ----------

    def search(self, query: str, top_k: int = 5) -> List[Hit]:
        query = query.strip()
        if not query:
            return []
        hits: List[Hit] = []

        # 1) 名称/别名精确匹配
        for obj in self._names:
            if query == obj["name"] or query in obj.get("aliases", []):
                hits.append(self._hit(obj, 100.0, "exact"))

        # 2) 名称/别名包含匹配（双向：查询含名称，或名称含查询）
        for obj in self._names:
            if any(h.id == obj["id"] for h in hits):
                continue
            names = [obj["name"]] + obj.get("aliases", [])
            if any(query in nm or nm in query for nm in names):
                hits.append(self._hit(obj, 60.0, "contains"))

        # 3) FTS 兜底（定义文本）
        seen = {h.id for h in hits}
        for row in self._fts(query, top_k * 3):
            if row["id"] in seen:
                continue
            obj = next((o for o in self._names if o["id"] == row["id"]), None)
            if obj:
                hits.append(self._hit(obj, float(row["rank"]), "fts"))
                seen.add(obj["id"])

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]

    def _hit(self, obj: dict, score: float, match_type: str) -> Hit:
        return Hit(
            id=obj["id"], name=obj["name"], grade=obj.get("grade", ""),
            definition=obj.get("definition", ""),
            score=score, match_type=match_type,
        )

    def _fts(self, query: str, limit: int) -> List[sqlite3.Row]:
        tokens = _space(query).split()
        if not tokens:
            return []
        match = " ".join(f'"{t}"' for t in tokens)  # AND 语义
        try:
            return self.con.execute(
                "SELECT id, bm25(kb_fts) AS rank FROM kb_fts "
                "WHERE kb_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

    def get(self, concept_id: str) -> Optional[dict]:
        for obj in self._names:
            if obj["id"] == concept_id:
                return obj
        return None

    def close(self) -> None:
        self.con.close()


if __name__ == "__main__":
    r = KBRetriever()
    for q in ["勾股定理", "分式", "一元二次方程的求根公式", "三角形全等的判定",
              "斜率", "韦达定理"]:
        print(f"\n查询「{q}」:")
        for h in r.search(q, top_k=3):
            print(f"  [{h.match_type:9s}] {h.name}（{h.grade}）")
