"""可选的 Web 应用：上传教学设计文件 + 声明元数据 → 可视化评估报告。

运行：edu-eval-web  （或 python -m edu_eval.web）
访问：http://127.0.0.1:8000
所有密钥来自环境变量，Web 层不读取、不存储任何 API Key。

P0-8（M2 里程碑）：mock 模式即可演示完整报告流程——
维度卡片 + 证据引用 + 雷达图（后端渲染 SVG，无前端依赖）。
"""
from __future__ import annotations

import html
import os
import tempfile

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .config import Hy3Config
from .eval import dimensions as D
from .eval.run_eval import EvalContext, evaluate
from .eval.rubric import admission_badge, radar_svg, score_band

app = FastAPI(title="EduEval（基于混元 Hy3 · 个人/活动作品）")

_STYLE = """
 body{font-family:-apple-system,"PingFang SC",sans-serif;max-width:920px;margin:32px auto;padding:0 16px;color:#1f2328;background:#fff}
 h1{font-size:22px} .muted{color:#57606a;font-size:13px}
 form{border:1px solid #d0d7de;border-radius:10px;padding:18px;margin:16px 0}
 label{display:block;margin:10px 0 4px;font-weight:600}
 input[type=text]{width:100%;padding:7px;border:1px solid #d0d7de;border-radius:6px;box-sizing:border-box}
 button{margin-top:14px;background:#1f6feb;color:#fff;border:0;padding:9px 18px;border-radius:6px;cursor:pointer;font-size:14px}
 .warn{background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:8px 12px;font-size:13px}
 .card{border:1px solid #d0d7de;border-radius:10px;padding:14px 18px;margin:12px 0}
 .badge{display:inline-block;color:#fff;border-radius:12px;padding:2px 12px;font-size:13px;font-weight:600}
 .score-bar{height:8px;background:#f6f8fa;border-radius:4px;overflow:hidden;margin:6px 0}
 .score-bar>div{height:100%;border-radius:4px}
 .ev{border-left:3px solid #d0d7de;padding:2px 10px;margin:6px 0;color:#57606a;font-size:13px;background:#f9fafb}
 .grid{display:grid;grid-template-columns:300px 1fr;gap:18px;align-items:start}
 .total{font-size:40px;font-weight:700}
 .ne-tag{color:#8b949e;font-size:12px;border:1px solid #d0d7de;border-radius:4px;padding:1px 6px}
 .fail-ev{border-left:3px solid #cf222e;padding:4px 10px;margin:6px 0;font-size:13px;background:#ffebe9}
 ul{padding-left:18px}
"""

PAGE = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>EduEval · 初中数学教学设计评估</title>
<style>{_STYLE}</style></head><body>
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
<p class="muted">命令行用法：edu-eval 文件 --grade 七年级 --version 人教版 --topic 一元一次方程 --period 1课时 ｜ JSON API：POST /api/evaluate</p>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


async def _run_eval(file: UploadFile, grade: str, version: str, topic: str,
                    period: str):
    suffix = os.path.splitext(file.filename or "doc.txt")[1].lower() or ".txt"
    with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=False) as buf:
        buf.write(await file.read())
        tmppath = buf.name
    try:
        cfg = Hy3Config.from_env(require_key=True)
        ctx = EvalContext(grade=grade, version=version, topic=topic, period=period)
        report = evaluate(tmppath, cfg, ctx)
        return report
    finally:
        os.unlink(tmppath)


@app.post("/api/evaluate")
async def api_evaluate(file: UploadFile = File(...), grade: str = Form(""),
                       version: str = Form(""), topic: str = Form(""),
                       period: str = Form("")) -> JSONResponse:
    report = await _run_eval(file, grade, version, topic, period)
    return JSONResponse(content=report.to_dict())


@app.post("/evaluate", response_class=HTMLResponse)
async def evaluate_endpoint(
    file: UploadFile = File(...),
    grade: str = Form(""),
    version: str = Form(""),
    topic: str = Form(""),
    period: str = Form(""),
) -> str:
    report = await _run_eval(file, grade, version, topic, period)
    return _render_report(report, file.filename or "未命名")


def _render_report(report, filename: str) -> str:
    d = report.to_dict()
    agg = d.get("aggregation", {})
    badge = admission_badge(d["admission"], d.get("redline", False))
    total = agg.get("total_score")
    total_html = (f'<div class="total">{total}<span style="font-size:16px;'
                  f'color:#57606a"> / 100</span></div>'
                  if total is not None else
                  f'<div class="total" style="font-size:22px;color:#8b949e">—</div>')

    # 规则层发现（P0-10 · B：直接消费结构化 rules，不再从 suggestions 反推）
    rules_html = ""
    rule_rep = d.get("rules") or {}
    rule_findings = rule_rep.get("findings") or []
    if rule_findings or rule_rep.get("g0_rule_verdict"):
        rows = []
        mark = {"fail": ("✗", "#cf222e"), "warn": ("△", "#9a6700"),
                "ne": ("·", "#8b949e"), "pass": ("✓", "#1a7f37")}
        for f in rule_findings:
            sym, color = mark.get(f.get("verdict"), ("·", "#8b949e"))
            ev = html.escape((f.get("evidence") or
                              (f.get("detail") or {}).get("reason") or "")[:200])
            rows.append(
                f'<div class="ev" style="border-left-color:{color}">'
                f'<b style="color:{color}">{sym} {html.escape(f.get("rule_id", ""))}</b>'
                f"　{ev}</div>")
        g0 = rule_rep.get("g0_rule_verdict", "NE")
        rules_html = (
            f'<div class="card"><h3>规则层确定性检查（零 LLM）'
            f'　G0：{html.escape(str(g0))}</h3>{"".join(rows)}</div>')

    # 环境告警（P0-10 · A/G：静默降级改为显式提示）
    warns = d.get("warnings") or []
    if warns:
        w_html = "".join(f'<div class="warn" style="margin:6px 0">⚠ {html.escape(w)}</div>'
                         for w in warns)
        rules_html += f'<div class="card"><h3>环境告警</h3>{w_html}</div>'

    # 维度卡片
    cards = []
    for dim in D.DIMENSIONS:
        s = d.get("scores", {}).get(dim.id)
        if not isinstance(s, dict):
            continue
        band = score_band(None if s.get("ne") else s.get("score"))
        ev = html.escape((s.get("evidence") or "")[:300])
        ne = '<span class="ne-tag">NE</span>' if s.get("ne") else ""
        pct = (s.get("score", 0) / 5 * 100) if not s.get("ne") else 0
        arb = ' <span class="ne-tag">仲裁</span>' if s.get("arbitrated") else ""
        cards.append(f"""
<div class="card">
 <div style="display:flex;justify-content:space-between;align-items:center">
  <b>维度 {dim.id} · {html.escape(dim.name)}{arb}</b>
  <span class="badge" style="background:{band['color']}">{band['label']}{ne}</span>
 </div>
 <div class="muted">优先级 {dim.priority} ｜ 权重 {dim.weight}%{ '（辅助，不计分）' if dim.auxiliary else ''}</div>
 <div class="score-bar"><div style="width:{pct:.0f}%;background:{band['color']}"></div></div>
 {f'<div class="ev">证据：{ev}</div>' if ev else ''}
</div>""")

    radar = radar_svg(d.get("scores", {}))
    suggestions = "".join(f"<li>{html.escape(s)}</li>"
                          for s in d.get("suggestions", []))
    arbitration = (f'<p class="muted">仲裁维度：{", ".join(d["arbitration"])}</p>'
                   if d.get("arbitration") else "")
    p = d.get("parse", {})
    parse_line = (f'解析状态 {p.get("parse_status")}（置信度 '
                  f'{p.get("parse_confidence")}）' if p else "")

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>评估报告 · {html.escape(filename)}</title>
<style>{_STYLE}</style></head><body>
<h1>评估报告 <span class="muted">{html.escape(filename)}</span></h1>
<p class="muted">{parse_line} ｜ 知识库命中 {d.get("kb_hits", 0)} 条</p>
<div class="grid">
 <div>
  <div class="card" style="text-align:center">
   <span class="badge" style="background:{badge['bg']}">{badge['label']}</span>
   {total_html}
   <div style="font-size:15px">{agg.get("grade") or ""}</div>
   <div class="muted">{html.escape(agg.get("verdict") or "")} {html.escape(agg.get("reason") or "")}</div>
  </div>
  <div class="card" style="text-align:center">{radar}</div>
 </div>
 <div>
  {rules_html}
  {''.join(cards)}
  {arbitration}
  {'<div class="card"><h3>改进建议</h3><ul>' + suggestions + '</ul></div>' if suggestions else ''}
 </div>
</div>
<p class="muted" style="margin-top:24px"><a href="/">← 返回上传</a> ｜ EduEval（基于混元 Hy3 · 个人/活动作品）</p>
</body></html>"""


def main() -> None:  # pragma: no cover
    import uvicorn
    D.validate_weights()
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
