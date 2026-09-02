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


# FTS 兜底打分参数：|bm25| → (0,20)，低于阈值的判定为逐字噪声
# 实测：正确关联 |bm25|≈12~23（分数 8.9~12.1）；噪声「积分」|bm25|≈5~8（分数 5~7）
FTS_SCALE = 15.0
FTS_MIN_SCORE = 8.0


class KBRetriever:
    def __init__(self, db_path: Optional[str] = None,
                 jsonl_path: Optional[str] = None):
        # 默认路径经 paths 解析，不依赖当前工作目录（P0-10 · G）
        if db_path is None:
            from edu_eval import paths as P

            db_path = P.kb_db()
        if not os.path.exists(db_path):
            raise FileNotFoundError(
                f"知识库索引 {db_path} 不存在，请先运行: python -m edu_eval.kb.ingest"
            )
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        # 名称/别名精确与包含匹配用附属表（含 FTS 之外的原始字段）
        if jsonl_path is None:
            from edu_eval import paths as P

            jsonl_path = P.kb_jsonl()
        self._load_names(jsonl_path)
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

        # 2) 名称/别名包含匹配（双向），按匹配长度加权
        #    说明：早期版本对 contains 一律给 60 分，导致单字概念（线/点/面/弦/0）
        #    与长概念同分、靠前胜出——「抛物线」→「线」、「余弦」→「弦」。
        #    现改为匹配越长分越高，并对多字查询过滤单字概念。
        for obj in self._names:
            if any(h.id == obj["id"] for h in hits):
                continue
            names = [obj["name"]] + list(obj.get("aliases") or [])
            best = 0
            for nm in names:
                if not nm:
                    continue
                if len(query) > 1 and len(nm) < 2:
                    continue  # 多字查询下，单字概念不参与子串匹配
                if nm in query:
                    best = max(best, len(nm))        # 概念名出现在句中
                elif query in nm:
                    best = max(best, len(query))     # 查询是概念名的子串
            if best:
                hits.append(self._hit(
                    obj, 40.0 + min(best, 8) * 7.5, "contains"))

        # 3) FTS 兜底（定义文本）
        seen = {h.id for h in hits}
        for row in self._fts(query, top_k * 3):
            if row["id"] in seen:
                continue
            obj = next((o for o in self._names if o["id"] == row["id"]), None)
            if obj:
                # SQLite FTS5 的 bm25() 返回负值，且**越负越相关**（故 ORDER BY rank 升序）。
                # 早期版本把负值直接当作分数参与降序排序 —— 方向反了，最相关的排到了最后。
                # 现单调映射为 (0,20)：|rank| 越大分越高，且上限低于 contains 下限（55），
                # 保持兜底定位；低于 FTS_MIN_SCORE 的视为噪声（如「积分」→「积的乘方」）。
                rank = abs(float(row["rank"]))
                score = 20.0 * rank / (rank + FTS_SCALE)
                if score >= FTS_MIN_SCORE:
                    hits.append(self._hit(obj, score, "fts"))
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
