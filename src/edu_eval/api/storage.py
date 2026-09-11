"""SQLite 历史记录持久化。

路径优先级：
  1. 环境变量 EDU_EVAL_DEMO_DB 显式指定
  2. ./results/demo.db（仓库内，gitignore）
  3. 系统临时目录（兜底）

启动时会做一次「写探针」：候选路径逐个试建表，第一个可用的胜出。
这样在受限环境（如沙箱拦截仓库目录写入）下会自动降级到 /tmp，
历史功能照常可用，而不是抛 disk I/O error 把端点打挂。

所有对外函数在 sqlite3 出错时降级（返回空/None），绝不把 500 抛给前端。
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


_lock = threading.Lock()


def _candidates() -> List[Path]:
    """按优先级返回候选 DB 路径。"""
    out: List[Path] = []
    env = os.environ.get("EDU_EVAL_DEMO_DB", "").strip()
    if env:
        out.append(Path(env).expanduser())
    out.append(Path("./results/demo.db"))
    out.append(Path(tempfile.gettempdir()) / "edu_eval_demo" / "demo.db")
    return out


def _probe(path: Path) -> Optional[str]:
    """试写探针：返回 None 表示可用，否则返回错误描述。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _probe (x INTEGER)")
            conn.execute("INSERT INTO _probe VALUES (1)")
            conn.execute("DROP TABLE _probe")
            conn.commit()
        finally:
            conn.close()
        return None
    except Exception as exc:  # noqa: BLE001 - 探针要吞掉一切，交给下一个候选
        return f"{type(exc).__name__}: {exc}"


def _resolve() -> tuple[Path, bool, str]:
    """选出第一个可写候选。全部失败时返回最后一个路径 + 首个错误原因。"""
    cands = _candidates()
    first_err = ""
    for p in cands:
        err = _probe(p)
        if err is None:
            note = "" if p == cands[0] else f"首选路径不可写，已降级到 {p}"
            return p, True, note
        if not first_err:
            first_err = err
    return cands[-1], False, f"所有候选路径均不可写（{first_err}）"


DB_PATH, DB_WRITABLE, DB_NOTE = _resolve()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    file_name TEXT,
    grade TEXT,
    admission TEXT,
    dual_sample INTEGER NOT NULL,
    total_score REAL,
    source_text TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at DESC);
"""

#: 源文本入库上限（字符）。报告要能「点开看原文」，但正文可能是几十页 PDF 抽取物，
#: 全量塞进 DB 会让历史列表的 payload 拖得又大又慢。超出部分截断并留痕。
MAX_SOURCE_CHARS = 400_000


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    """打开连接并在退出时**关闭**。

    注意：`with sqlite3.connect(...)` 只提交/回滚事务，**不关闭连接**，
    长期运行的服务会持续泄漏文件描述符，必须显式 close()。
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=10.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _migrate(c: sqlite3.Connection) -> None:
    """轻量迁移：给已存在的旧表补新列。

    `CREATE TABLE IF NOT EXISTS` 对老库什么都不做，所以新增列必须显式补——
    否则老库上带 source_text 的 INSERT 会直接 `no such column` 报错。
    用 PRAGMA table_info 判存在性，ADD COLUMN 本身可重复执行。
    """
    cols = {row[1] for row in c.execute("PRAGMA table_info(reports)").fetchall()}
    if "source_text" not in cols:
        c.execute("ALTER TABLE reports ADD COLUMN source_text TEXT")


def init_db() -> None:
    """进程启动时调用一次：建表 + 建索引 + 补列。失败只记 warning，不阻断启动。"""
    if not DB_WRITABLE:
        return
    with _lock:
        try:
            with _conn() as c:
                c.executescript(_SCHEMA)
                _migrate(c)
                c.commit()
        except sqlite3.Error as exc:
            print(f"[storage] 建表失败（历史功能不可用）：{exc}")


def db_status() -> Dict[str, Any]:
    """给 /api/health 用：暴露 DB 实际落点与可写状态，便于排查。"""
    return {
        "db_path": str(DB_PATH),
        "db_writable": DB_WRITABLE,
        "db_note": DB_NOTE,
        "db_count": count_reports(),
    }


def add_report(payload: Dict[str, Any], *, file_name: str = "",
               grade: str = "", dual_sample: bool = True,
               source_text: str = "") -> Optional[int]:
    """写入一条报告，返回 id；失败返回 None（调用方降级，不影响主流程）。

    source_text 是**被评测的那份课件正文**，单独成一列而不是塞进 payload：
    报告详情在历史列表里会被反复读取，正文可达数百 KB，挂在 payload 上等于
    每次列表都拖着正文跑。它只在「查看源文件」时按需取。
    """
    if not DB_WRITABLE:
        return None
    total = (payload.get("aggregation") or {}).get("total_score")
    if isinstance(total, bool) or not isinstance(total, (int, float)):
        total = None
    src = (source_text or "")[:MAX_SOURCE_CHARS]
    with _lock:
        try:
            with _conn() as c:
                cur = c.execute(
                    """INSERT INTO reports
                       (created_at, file_name, grade, admission, dual_sample,
                        total_score, source_text, payload)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (time.time(), (file_name or "")[:120], grade,
                     payload.get("admission", "") or "",
                     1 if dual_sample else 0,
                     total,
                     src,
                     json.dumps(payload, ensure_ascii=False)),
                )
                c.commit()
                return cur.lastrowid
        except (sqlite3.Error, TypeError, ValueError) as exc:
            print(f"[storage] 写入历史失败：{exc}")
            return None


def list_reports(limit: int = 50) -> List[Dict[str, Any]]:
    """列出历史报告摘要（不含 payload）。失败返回空列表，不抛异常。"""
    if not DB_WRITABLE:
        return []
    with _lock:
        try:
            with _conn() as c:
                rows = c.execute(
                    """SELECT id, created_at, file_name, grade, admission,
                              dual_sample, total_score
                       FROM reports ORDER BY id DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.Error as exc:
            print(f"[storage] 读取历史失败：{exc}")
            return []


def get_report(rid: int) -> Optional[Dict[str, Any]]:
    """按 id 取报告完整 payload。不存在或出错返回 None。"""
    if not DB_WRITABLE:
        return None
    with _lock:
        try:
            with _conn() as c:
                row = c.execute(
                    "SELECT payload FROM reports WHERE id = ?", (rid,)
                ).fetchone()
        except sqlite3.Error as exc:
            print(f"[storage] 读取报告失败：{exc}")
            return None
    if row is None:
        return None
    try:
        payload = json.loads(row["payload"])
    except (json.JSONDecodeError, TypeError):
        return None
    payload["_id"] = rid
    return payload


def get_report_source(rid: int) -> Optional[tuple]:
    """按 id 取历史报告存下的**源文件正文**。

    返回 (content, kind) 二元组，kind 固定为 \"report\"；
    记录不存在 / 该记录没存正文（旧版本写的）/ 读取出错，返回 None。

    与 get_report 分开：正文只在「查看源文件」时按需取，不跟着报告详情返回。
    """
    if not DB_WRITABLE:
        return None
    with _lock:
        try:
            with _conn() as c:
                row = c.execute(
                    "SELECT source_text FROM reports WHERE id = ?", (rid,)
                ).fetchone()
        except sqlite3.Error as exc:
            print(f"[storage] 读取源文本失败：{exc}")
            return None
    if row is None:
        return None
    src = row["source_text"]
    if not src:
        return None
    return (src, "report")


def count_reports() -> int:
    """历史总数。失败返回 0。"""
    if not DB_WRITABLE:
        return 0
    with _lock:
        try:
            with _conn() as c:
                return int(c.execute("SELECT COUNT(*) FROM reports").fetchone()[0])
        except sqlite3.Error:
            return 0
