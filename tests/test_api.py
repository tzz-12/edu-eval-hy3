"""API 层（FastAPI + SQLite 历史）回归测试。

覆盖：
- storage 路径探测与降级、连接不泄漏
- health / grades / demo-samples 只读端点
- evaluate demo 模式（含非法 id 防护）
- evaluate live 模式（mock 后端）+ 历史回写
- 历史列表 / 详情 / 404
- file_name 派生规则

注意：storage 在 import 时即解析 DB 路径，测不同路径必须 importlib.reload。
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from edu_eval.api import storage  # noqa: E402
from edu_eval.api.routes import evaluate as evaluate_route  # noqa: E402
from edu_eval.api.routes import health as health_route  # noqa: E402


# ---------------------------------------------------------------- storage


def _reload_storage(db_path: str, monkeypatch):
    monkeypatch.setenv("EDU_EVAL_DEMO_DB", db_path)
    return importlib.reload(storage)


def test_storage_resolves_env_path(monkeypatch, tmp_path):
    """显式指定路径时直接用它，不降级。"""
    p = tmp_path / "sub" / "demo.db"
    st = _reload_storage(str(p), monkeypatch)
    assert st.DB_WRITABLE is True
    assert st.DB_PATH == p
    assert st.DB_NOTE == ""          # 首选可用 → 无降级提示
    assert p.parent.exists()         # 自动建父目录


def test_storage_falls_back_when_path_unwritable(monkeypatch, tmp_path):
    """首选路径不可写（例如目录不存在且无法创建）时降级，而不是硬崩。"""
    # 用一个「文件」冒充目录，mkdir 必失败 → 探针报错 → 降级
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("x", encoding="utf-8")
    st = _reload_storage(str(blocker / "demo.db"), monkeypatch)
    assert st.DB_WRITABLE is True
    assert st.DB_PATH != blocker / "demo.db"
    assert "降级" in st.DB_NOTE


def test_storage_crud_roundtrip(monkeypatch, tmp_path):
    """写入 → 列表 → 详情 → 计数 全链路。"""
    st = _reload_storage(str(tmp_path / "db" / "demo.db"), monkeypatch)
    st.init_db()
    payload = {"admission": "PASS", "aggregation": {"total_score": 88.0}}
    rid = st.add_report(payload, file_name="a.md", grade="九年级", dual_sample=True)
    assert rid is not None

    rows = st.list_reports()
    assert len(rows) == 1
    assert rows[0]["id"] == rid
    assert rows[0]["file_name"] == "a.md"
    assert rows[0]["total_score"] == 88.0
    assert rows[0]["dual_sample"] == 1

    got = st.get_report(rid)
    assert got is not None and got["_id"] == rid
    assert got["admission"] == "PASS"
    assert st.count_reports() == 1
    assert st.get_report(rid + 999) is None


def test_storage_does_not_leak_connections(monkeypatch, tmp_path):
    """回归：旧实现用 `with sqlite3.connect()` 不关连接，长跑会泄漏 FD。"""
    st = _reload_storage(str(tmp_path / "db" / "demo.db"), monkeypatch)
    st.init_db()

    def open_fds() -> int:
        try:
            return len(os.listdir(f"/dev/fd"))
        except OSError:  # pragma: no cover - 非 POSIX
            return 0

    st.count_reports()
    before = open_fds()
    for _ in range(60):
        st.count_reports()
    after = open_fds()
    assert after - before <= 5, f"连接疑似泄漏：FD 从 {before} 涨到 {after}"


def test_storage_degrades_instead_of_raising(monkeypatch, tmp_path):
    """DB 被删后读写应降级为空/None，不能把异常抛给端点（曾导致 /api/reports 500）。"""
    db = tmp_path / "db" / "demo.db"
    st = _reload_storage(str(db), monkeypatch)
    st.init_db()
    st.add_report({"admission": "PASS"}, file_name="x.md", grade="九年级")
    db.unlink()                       # 模拟磁盘故障

    # 关键：这些调用都不许抛
    rows = st.list_reports()
    assert isinstance(rows, list)
    assert st.get_report(1) in (None, {}) or isinstance(st.get_report(1), dict)
    st.init_db()                      # 重建也不能抛


# ---------------------------------------------------------------- endpoints


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """构造一个隔离的 TestClient：DB / 缓存 / 演示目录全指向 tmp。"""
    monkeypatch.setenv("EDU_EVAL_DEMO_DB", str(tmp_path / "db" / "demo.db"))
    monkeypatch.setenv("EDU_EVAL_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("EDU_EVAL_DEMO_DIR", str(tmp_path / "demo_reports"))
    monkeypatch.setenv("HY3_API_KEY", "test-key-not-real")
    monkeypatch.setenv("HY3_MOCK", "1")
    monkeypatch.setenv("HY3_MODEL", "mock-model")

    st = importlib.reload(storage)
    st.init_db()
    # 三个模块都在 import 期解析路径常量，必须全部重载才能让本 fixture 的
    # 环境变量生效（否则会读到仓库里真实的 data/demo_reports）
    ev = importlib.reload(evaluate_route)
    hl = importlib.reload(health_route)

    from edu_eval.api.main import fastapi_app
    with TestClient(fastapi_app) as c:
        yield c, ev, st


def test_health_exposes_db_status(client):
    c, _ev, _st = client
    r = c.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["api_key_configured"] is True
    assert d["db_writable"] is True
    assert str(_st.DB_PATH) == d["db_path"]
    assert isinstance(d["db_count"], int)


def test_grades_and_demo_samples(client, tmp_path):
    c, _ev, _st = client
    assert c.get("/api/grades").json()["grades"][0] == "七年级"

    # 空目录 → 空列表，不报错
    assert c.get("/api/demo-samples").json()["samples"] == []

    # 放一份假报告进去 → 应被列出（前端靠这个渲染快捷入口）
    (tmp_path / "demo_reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "demo_reports" / "01_demo_x.json").write_text(
        json.dumps({"admission": "PASS", "aggregation": {"total_score": 90.0}}),
        encoding="utf-8",
    )
    samples = c.get("/api/demo-samples").json()["samples"]
    assert len(samples) == 1
    assert samples[0]["id"] == "01_demo_x"
    assert samples[0]["total_score"] == 90.0


def test_evaluate_demo_mode_returns_pregen(client, tmp_path):
    c, _ev, _st = client
    (tmp_path / "demo_reports").mkdir(parents=True, exist_ok=True)
    report = {
        "admission": "FAIL",
        "aggregation": {"total_score": None},
        "rules": {"findings": [{"rule_id": "R-FORMULA"}]},
        "scores": {},
    }
    (tmp_path / "demo_reports" / "bad.json").write_text(
        json.dumps(report), encoding="utf-8")

    r = c.post("/api/evaluate", json={"source": "demo:bad", "grade": "九年级"})
    assert r.status_code == 200
    d = r.json()
    assert d["admission"] == "FAIL"
    assert d["_source"] == "demo:bad"
    # 演示报告不得污染历史库
    assert d.get("_id") is None
    assert c.get("/api/reports").json() == []


def test_evaluate_demo_rejects_path_traversal(client):
    c, _ev, _st = client
    for bad in ["demo:../../etc/passwd", "demo:a/b"]:
        r = c.post("/api/evaluate", json={"source": bad, "grade": "九年级"})
        assert r.status_code == 400, f"{bad} 应被拒绝"


def test_evaluate_demo_missing_report_404(client, tmp_path):
    c, _ev, _st = client
    (tmp_path / "demo_reports").mkdir(parents=True, exist_ok=True)
    r = c.post("/api/evaluate", json={"source": "demo:nope", "grade": "九年级"})
    assert r.status_code == 404


def test_evaluate_live_requires_text(client):
    c, _ev, _st = client
    r = c.post("/api/evaluate", json={"text": "   ", "grade": "九年级"})
    assert r.status_code == 400
    r = c.post("/api/evaluate", json={"grade": "九年级"})
    assert r.status_code == 400


def test_evaluate_live_writes_history(client):
    """live 模式真跑（mock 后端）并落库；file_name 取正文首个一级标题。"""
    c, _ev, _st = client
    text = "# 相似三角形的判定\n## 教学目标\n掌握 AA 判定。\n## 评价设计\n课堂练习。\n"
    r = c.post("/api/evaluate", json={
        "text": text, "grade": "九年级", "source": "live", "dual_sample": False,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["_source"] == "live"
    assert d["_id"] is not None

    rows = c.get("/api/reports").json()
    assert len(rows) == 1
    assert rows[0]["file_name"] == "相似三角形的判定"   # 不是正文前 N 字
    assert rows[0]["grade"] == "九年级"

    got = c.get(f"/api/reports/{rows[0]['id']}").json()
    assert got["admission"] == d["admission"]


def test_evaluate_live_without_api_key_503(client, monkeypatch):
    c, _ev, _st = client
    monkeypatch.delenv("HY3_API_KEY", raising=False)
    r = c.post("/api/evaluate", json={
        "text": "# t\n## 教学目标\nx。", "grade": "九年级", "source": "live"})
    assert r.status_code == 503


def test_reports_404_for_unknown_id(client):
    c, _ev, _st = client
    assert c.get("/api/reports/9999").status_code == 404


# ---------------------------------------------------------------- helpers


def test_derive_name_prefers_explicit_then_heading():
    from edu_eval.api.routes.evaluate import _derive_name

    assert _derive_name("上传.md", "# 标题") == "上传.md"
    assert _derive_name("  ", "# 一次函数\n正文") == "一次函数"
    assert _derive_name(None, "没有标题的纯文本") == "粘贴文本"
    assert _derive_name(None, "") == "粘贴文本"
    # 显式名过长要截断，别把整篇课文塞进历史表
    assert len(_derive_name("x" * 500, "")) <= 81


def test_sanitize_payload_handles_sympy():
    """回归：规则层 sympy 反例（Integer）曾导致 JSON 序列化崩溃。

    更早的实现用 `isinstance(obj, sympy.BoolAtom)`，而 sympy 1.14 没有这个
    顶层属性 —— 一碰到 sympy 对象就 AttributeError，比不清洗还糟。
    """
    import sympy
    from edu_eval.api.serialize import sanitize as _sanitize_payload

    raw = {"n": sympy.Integer(4), "f": sympy.Float(1.5),
           "b": sympy.S.true, "lst": [sympy.Integer(2)],
           "nested": {"k": sympy.Integer(7)},
           "expr": sympy.Symbol("x") ** 2}
    out = _sanitize_payload(raw)
    json.dumps(out)                                  # 必须可序列化
    assert out["n"] == 4 and isinstance(out["n"], int)
    assert out["f"] == 1.5 and isinstance(out["f"], float)
    assert out["lst"] == [2]
    assert out["nested"]["k"] == 7
    assert out["b"] is True and isinstance(out["b"], bool)
    assert out["expr"] == "x**2"                     # 非数值表达式退化成字符串


def test_sanitize_payload_never_raises():
    """兜底契约：任何奇怪对象都必须能过，宁可字符串化也不抛。"""
    from edu_eval.api.serialize import sanitize

    class Weird:
        def __str__(self):
            raise RuntimeError("boom")

    out = sanitize({"set": {1, 2}, "weird": Weird(), "ok": 1})
    assert out["ok"] == 1
    assert sorted(out["set"]) == [1, 2]
    assert out["weird"] is None                      # str() 也炸 → None


def test_json_default_used_by_pregen():
    """pregen 脚本与 API 端点共用同一套序列化策略，避免两份实现漂移。"""
    import sympy
    from edu_eval.api.serialize import json_default

    json.dumps({"n": sympy.Integer(9), "s": {3, 1}}, default=json_default)
    assert json_default(sympy.Integer(9)) == 9


# ---------------------------------------------------------------- /api/manual


def test_manual_returns_full_payload(client):
    """说明面板端点必须返回完整口径：维度 / 权重 / 分档 / 分组 / 裁判模型。

    回归用例（2026-09-11）：把 _judge_info() 插在 `@router.get("/api/manual")`
    与 `manual()` 之间后，装饰器改贴到了新函数上——端点只返回
    `{"model": ..., "endpoint_host": ...}`，dimensions 变成空。
    当时全量测试没拦住，因为**此前没有任何 manual 端点的用例**。
    """
    c, _ev, _st = client
    r = c.get("/api/manual")
    assert r.status_code == 200
    d = r.json()
    assert len(d["dimensions"]) == 10
    assert d["weight_sum"] == 100
    assert d["grade_bands"], "分档阈值不能为空"
    assert d["judge_groups"], "Judge 分组不能为空"
    assert "judge" in d, "裁判模型是评测口径的一部分，必须随面板返回"
    assert d["judge"].get("model"), "裁判模型名不能为空"


def test_manual_dimensions_carry_weights_anchors_and_groups(client):
    """维度条目必须带全前端渲染所需字段（含 judge_group 归属）。"""
    c, _ev, _st = client
    dims = c.get("/api/manual").json()["dimensions"]
    ids = {d["id"] for d in dims}
    assert {"1", "2", "9", "A"} <= ids
    for d in dims:
        for key in ("name", "weight", "priority", "anchors", "judge_group", "in_total"):
            assert key in d, f"维度 {d['id']} 缺字段 {key}"
    # G0 闸门（维度 2）与辅助维度 A 都不计入总分权重
    by_id = {d["id"]: d for d in dims}
    assert by_id["2"]["weight"] == 0
    assert by_id["A"]["weight"] == 0


def test_manual_judge_is_not_a_route_of_its_own(client):
    """反方向：/api/manual 不能被 _judge_info 之类的小函数顶替。

    断言返回体里同时有 dimensions 与 judge —— 只返回 judge 的旧故障形态
    必然缺 dimensions，这条能立刻报错。
    """
    c, _ev, _st = client
    d = c.get("/api/manual").json()
    assert "dimensions" in d and "judge" in d
    assert set(d.keys()) != {"model", "endpoint_host", "mock"}
