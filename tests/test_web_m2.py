"""P0-8（M2 里程碑）：Web 端到端流程测试（mock 模式，无需 Hy3 Key）。"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("HY3_MOCK", "1")

from fastapi.testclient import TestClient  # noqa: E402

from edu_eval.web import app  # noqa: E402

SAMPLE = os.path.join(ROOT, "data", "samples", "example_lesson.md")


@pytest.fixture()
def client():
    return TestClient(app)


def test_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "EduEval" in r.text


def test_m2_full_report_html(client):
    """M2：上传 → 准入 → 评分 → 可视化报告（维度卡片+雷达图+证据）。"""
    with open(SAMPLE, "rb") as f:
        r = client.post(
            "/evaluate",
            files={"file": ("lesson.md", f, "text/markdown")},
            data={"grade": "七年级", "version": "人教版",
                  "topic": "一元一次方程", "period": "1课时"})
    assert r.status_code == 200
    assert "<svg" in r.text            # 雷达图
    assert "维度" in r.text              # 维度卡片
    assert "证据" in r.text or "演示模式" in r.text
    assert "知识库命中" in r.text


def test_api_json(client):
    with open(SAMPLE, "rb") as f:
        r = client.post(
            "/api/evaluate",
            files={"file": ("lesson.md", f, "text/markdown")},
            data={"grade": "七年级"})
    assert r.status_code == 200
    body = r.json()
    assert body["admission"] in ("PASS", "FAIL", "NE")
    assert "scores" in body and "aggregation" in body


def test_bad_doc_fail_html(client):
    """规则层硬错误样本：报告应展示规则层发现（M1 与 M2 联动）。"""
    bad = """# 教学设计
## 教学目标
理解公式。
## 教学重点
公式。
## 教学过程
板书 (a+b)²=a²+b²。
## 作业布置
练习。
"""
    r = client.post(
        "/evaluate",
        files={"file": ("bad.md", bad.encode(), "text/markdown")},
        data={"grade": "八年级", "version": "人教版", "topic": "整式的乘法"})
    assert r.status_code == 200
    body = r.json() if r.headers.get("content-type", "").startswith(
        "application/json") else None
    # HTML 端点：验证规则层发现渲染
    assert "规则层确定性检查" in r.text
    assert "不通过" in r.text or "准入" in r.text
