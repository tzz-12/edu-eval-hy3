"""源文件端点回归测试（GET /api/source）。

这个端点的真实风险不在「能不能取到」，而在两处：
① demo_id 直接来自 URL —— 必须挡住路径穿越，不能读到样本目录之外的任何文件；
② 老记录 / 演示报告没有源文本 —— 必须优雅降级，而不是 500 或把整页前端打挂。

注意：storage 在 import 期解析 DB 路径，测不同路径必须 importlib.reload；
source 端点按需读 `EDU_EVAL_SAMPLES_DIR`，所以样本目录能在 fixture 里隔离。
"""

from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from edu_eval.api import storage  # noqa: E402
from edu_eval.api.routes import evaluate as evaluate_route  # noqa: E402
from edu_eval.api.routes import source as source_route  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离环境：DB / 缓存 / 演示报告 / 样本源文件全指向 tmp。"""
    monkeypatch.setenv("EDU_EVAL_DEMO_DB", str(tmp_path / "db" / "demo.db"))
    monkeypatch.setenv("EDU_EVAL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("EDU_EVAL_DEMO_DIR", str(tmp_path / "demo_reports"))
    monkeypatch.setenv("EDU_EVAL_SAMPLES_DIR", str(tmp_path / "samples"))
    monkeypatch.setenv("HY3_API_KEY", "test-key-not-real")
    monkeypatch.setenv("HY3_MOCK", "1")
    monkeypatch.setenv("HY3_MODEL", "mock-model")
    (tmp_path / "samples").mkdir(parents=True, exist_ok=True)

    st = importlib.reload(storage)
    st.init_db()
    importlib.reload(evaluate_route)

    from edu_eval.api.main import fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, st, tmp_path


# ------------------------------------------------------------------ demo 路径


def test_source_demo_returns_text(client):
    """正路径：demo 报告的源文件就是仓库里那份底稿。"""
    c, _st, tmp = client
    (tmp / "samples" / "01_x.md").write_text("# 标题\n\n正文一段。", encoding="utf-8")

    r = c.get("/api/source", params={"demo_id": "01_x"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["source_kind"] == "demo"
    assert d["content"].startswith("# 标题")
    assert d["length"] == len(d["content"])
    assert d["truncated"] is False


def test_source_demo_keeps_text_verbatim(client):
    """必须逐字返回：报告里的「原文证据」要能在这份文本里原样搜到。"""
    c, _st, tmp = client
    body = "环节一：教师提问「x² + 6x + 9 = 16 你能解吗？」\n\n| 开口 | 向上 |\n"
    (tmp / "samples" / "t.md").write_text(body, encoding="utf-8")
    d = c.get("/api/source", params={"demo_id": "t"}).json()
    assert d["content"] == body


@pytest.mark.parametrize("bad", [
    "../../etc/passwd",      # 经典穿越
    "..",                    # 只有上级
    "../secret",             # 越到同级目录
    "a/b",                   # 子路径
    "a\\b",                  # Windows 分隔符
    "01_x/../../x",          # 藏在合法前缀后的穿越
    "/etc/passwd",           # 绝对路径
    "",                      # 空
])
def test_source_demo_rejects_illegal_id(client, bad):
    """反方向：任何越出样本目录的 id 都必须在 400 处被拦住。"""
    c, _st, _tmp = client
    r = c.get("/api/source", params={"demo_id": bad})
    assert r.status_code == 400, f"{bad!r} 未被拦截：{r.status_code} {r.text[:120]}"


def test_source_demo_sibling_file_not_reachable(client):
    """把机密文件放在样本目录**旁边**，确认穿越真的够不着它。"""
    c, _st, tmp = client
    (tmp / "secret.md").write_text("TOP SECRET", encoding="utf-8")
    for bad in ["../secret", "..%2Fsecret", "....//secret"]:
        r = c.get("/api/source", params={"demo_id": bad})
        assert r.status_code == 400
        assert "TOP SECRET" not in r.text


def test_source_demo_missing_404(client):
    c, _st, _tmp = client
    r = c.get("/api/source", params={"demo_id": "nope"})
    assert r.status_code == 404
    assert "源文件不存在" in r.json()["detail"]


# ------------------------------------------------------------------ 参数校验


def test_source_requires_exactly_one_param(client):
    """两个都不给 / 两个都给 —— 都是调用方写错，必须 400 而不是猜一个。"""
    c, _st, _tmp = client
    assert c.get("/api/source").status_code == 400
    assert c.get("/api/source", params={"demo_id": "x", "report_id": 1}).status_code == 400


# ------------------------------------------------------- 历史报告（live）路径


def test_source_report_roundtrip(client):
    """live 评测把源文本一起落库，之后能原样取回。"""
    c, _st, _tmp = client
    text = "# 相似三角形的判定\n## 教学目标\n掌握 AA 判定。\n"
    r = c.post("/api/evaluate", json={
        "text": text, "grade": "九年级", "source": "live", "dual_sample": False,
    })
    assert r.status_code == 200, r.text
    rid = r.json()["_id"]
    assert rid is not None          # 落库成功才有源文件可看

    s = c.get("/api/source", params={"report_id": rid})
    assert s.status_code == 200, s.text
    d = s.json()
    assert d["source_kind"] == "report"
    assert d["content"] == text


def test_source_report_without_text_degrades(client):
    """边界：记录在、但没存源文本 —— 降级成一句人话，不能 500。"""
    c, st, _tmp = client
    rid = st.add_report({"admission": "PASS", "aggregation": {"total_score": 80.0}})

    r = c.get("/api/source", params={"report_id": rid})
    assert r.status_code == 200
    d = r.json()
    assert d["content"] == ""
    assert d["reason"]              # 前端直接把这句显示给用户


def test_source_report_unknown_id_404(client):
    c, _st, _tmp = client
    assert c.get("/api/source", params={"report_id": 999999}).status_code == 404


def test_source_truncates_oversized_text(client, monkeypatch):
    """超长正文要截断并置 truncated，避免一次把几十页塞给浏览器。"""
    c, _st, tmp = client
    monkeypatch.setattr(storage, "MAX_SOURCE_CHARS", 20)
    (tmp / "samples" / "big.md").write_text("字" * 100, encoding="utf-8")

    d = c.get("/api/source", params={"demo_id": "big"}).json()
    assert d["truncated"] is True
    assert d["length"] == 20


# ------------------------------------------------------------------ 表结构迁移


def test_storage_migration_adds_source_column(tmp_path, monkeypatch):
    """老库（没有 source_text 列）升级后仍能写入。

    没有这步迁移，`ALTER` 缺失 → 老用户升级后每条评测都落库失败，
    而落库失败是被静默降级的，表现成「历史里看不到记录」，很难排查。
    """
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            file_name TEXT, grade TEXT, admission TEXT,
            dual_sample INTEGER NOT NULL, total_score REAL,
            payload TEXT NOT NULL);
    """)
    conn.commit()
    conn.close()

    monkeypatch.setenv("EDU_EVAL_DEMO_DB", str(db))
    st = importlib.reload(storage)
    assert st.DB_WRITABLE is True

    st.init_db()                      # 补列
    rid = st.add_report({"admission": "PASS"}, source_text="原文正文")
    assert rid is not None
    assert st.get_report_source(rid) == ("原文正文", "report")

    st.init_db()                      # 幂等：重复执行不能报错
    assert st.get_report_source(rid) == ("原文正文", "report")


def test_storage_caps_source_text(tmp_path, monkeypatch):
    """入库时也要有上限——不能因为用户贴了一本书就把 DB 撑爆。"""
    monkeypatch.setenv("EDU_EVAL_DEMO_DB", str(tmp_path / "c.db"))
    st = importlib.reload(storage)
    st.init_db()
    monkeypatch.setattr(st, "MAX_SOURCE_CHARS", 5)
    rid = st.add_report({"admission": "PASS"}, source_text="0123456789")
    assert st.get_report_source(rid) == ("01234", "report")