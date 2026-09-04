"""SQLite 历史记录持久化。

存于 EDU_EVAL_DEMO_DB（默认 ./results/demo.db，gitignore）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DB_PATH = Path(os.environ.get("EDU_EVAL_DEMO_DB", "./results/demo.db"))
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    file_name TEXT,
    grade TEXT,
    admission TEXT,
    dual_sample INTEGER NOT NULL,
    total_score REAL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
"""


def init_db() -> None:
    """进程启动时调用一次：建表 + 建索引。"""
    with _lock:
        with _connect() as c:
            c.executescript(_SCHEMA)


def add_report(payload: Dict[str, Any], *, file_name: str = "",
               grade: str = "", dual_sample: bool = True) -> int:
    """写入一条报告，返回 id。"""
    total = (payload.get("aggregation") or {}).get("total_score")
    with _lock:
        with _connect() as c:
            cur = c.execute(
                """INSERT INTO reports
                   (created_at, file_name, grade, admission, dual_sample, total_score, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), file_name[:120], grade,
                 payload.get("admission", ""),
                 1 if dual_sample else 0,
                 total,
                 json.dumps(payload, ensure_ascii=False)),
            )
            return cur.lastrowid


def list_reports(limit: int = 50) -> List[Dict[str, Any]]:
    """列出历史报告摘要（不含 payload）。"""
    with _lock:
        with _connect() as c:
            rows = c.execute(
                """SELECT id, created_at, file_name, grade, admission,
                          dual_sample, total_score
                   FROM reports ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_report(rid: int) -> Optional[Dict[str, Any]]:
    """按 id 取报告完整 payload。"""
    with _lock:
        with _connect() as c:
            row = c.execute(
                "SELECT payload FROM reports WHERE id = ?", (rid,)
            ).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    payload["_id"] = rid
    return payload


def count_reports() -> int:
    with _lock:
        with _connect() as c:
            return c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
