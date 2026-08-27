"""可选的 Web 应用：上传教学设计文件 + 声明元数据 → 返回评估报告。

运行：edu-eval-web  （或 python -m edu_eval.web）
访问：http://127.0.0.1:8000
所有密钥来自环境变量，Web 层不读取、不存储任何 API Key。
"""
from __future__ import annotations

import os
import tempfile

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import Hy3Config
from .eval import dimensions as D
from .eval.knowledge_base import KnowledgeBase
from .eval.run_eval import EvalContext, evaluate

app = FastAPI(title="EduEval（基于混元 Hy3 · 个人/活动作品）")

PAGE = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>EduEval · 初中数学教学设计评估</title>
<style>
 body{font-family:-apple-system,"PingFang SC",sans-serif;max-width:820px;margin:40px auto;padding:0 16px;color:#1f2328}
 h1{font-size:22px} .muted{color:#57606a;font-size:13px}
 form{border:1px solid #d0d7de;border-radius:8px;padding:16px;margin:16px 0}
 label{display:block;margin:8px 0 4px;font-weight:600}
 input[type=text]{width:100%;padding:6px;border:1px solid #d0d7de;border-radius:6px;box-sizing:border-box}
 button{margin-top:12px;background:#1f6feb;color:#fff;border:0;padding:8px 16px;border-radius:6px;cursor:pointer}
 pre{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;padding:12px;overflow:auto;white-space:pre-wrap}
 .warn{background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:8px 12px;font-size:13px}
</style></head><body>
<h1>EduEval · 初中数学教学设计质量评估</h1>
<p class="muted">基于腾讯混元 Hy3 的评估器（个人 / 活动作品，非官方发布）。模型能力通过 Hy3 完成，不训练或微调模型。密钥仅来自服务端环境变量。</p>
<div class="warn">本页面为本地演示用，请勿上传含个人隐私或涉密内容的教学材料。</div>
<form action="/evaluate" method="post" enctype="multipart/form-data">
  <label>上传教学设计文件（.md/.txt/.docx/.pdf/.pptx）</label>
  <input type="file" name="file" required>
  <label>声明目标年级</label><input type="text" name="grade" placeholder="如 七年级">
  <label>声明教材版本</label><input type="text" name="version" placeholder="如 人教版">
  <label>声明课题</label><input type="text" name="topic" placeholder="如 一元一次方程">
  <label>声明课时</label><input type="text" name="period" placeholder="如 1课时">
  <button type="submit">开始评估</button>
</form>
<p class="muted">命令行用法：edu-eval 文件 --grade 七年级 --version 人教版 --topic 一元一次方程 --period 1课时</p>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.post("/evaluate")
async def evaluate_endpoint(
    file: UploadFile = File(...),
    grade: str = Form(""),
    version: str = Form(""),
    topic: str = Form(""),
    period: str = Form(""),
):
    suffix = os.path.splitext(file.filename or "doc.txt")[1].lower() or ".txt"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as buf:
        buf.write(await file.read())
        tmppath = buf.name
    try:
        cfg = Hy3Config.from_env(require_key=True)
        kb = KnowledgeBase([])
        ctx = EvalContext(grade=grade, version=version, topic=topic, period=period)
        report = evaluate(tmppath, cfg, ctx, kb)
    finally:
        os.unlink(tmppath)
    return JSONResponse(content=report.to_dict())


def main() -> None:  # pragma: no cover
    import uvicorn
    D.validate_weights()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
