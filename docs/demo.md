# EduEval Demo · 部署与启动

把 EduEval 评测器打包成一个 FastAPI + 单页前端的应用，本地一键启动。

## 1. 准备环境

仓库根目录的 `.env`（gitignored）需配置：

```ini
HY3_BASE_URL=https://openrouter.ai/api/v1
HY3_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
HY3_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
HY3_MAX_TOKENS=8192
HY3_TIMEOUT=300
```

依赖：Python 3.10+、FastAPI 0.141、uvicorn 0.52、pymupdf（解析 PDF）。

## 2. 一键启动

```bash
bash scripts/run_demo.sh start        # 默认 8000 端口
bash scripts/run_demo.sh start 8765   # 自定义端口
bash scripts/run_demo.sh status
bash scripts/run_demo.sh tail         # 看日志
bash scripts/run_demo.sh stop
```

启动后浏览器打开 `http://localhost:8000`。

## 3. 演示快捷入口（秒级）

预生成 3 个样本的评测报告，作为「好 vs 坏」对照演示，**无需等待真实评测**。

```bash
set -a; source .env; set +a
python scripts/pregen_demo_reports.py
```

输出到 `data/demo_reports/`：
- `01_good_二次函数.json` —— 好样本，期望 PASS
- `02_bad_formula.json` —— 注入公式错误，期望 FAIL（G0 红线）
- `03_bad_fake_socratic.json` —— 注入伪启发包装，期望启发引导维度低分

> OpenRouter 免费层每日 50 次额度（`free-models-per-day`），预生成 3 份报告
> 会消耗约 80-120 次 API 调用（双采样模式）。建议额度重置后跑。
> 已生成的报告可重复使用，不会再次扣额度。

## 4. API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 不暴露 key，仅返回 `{ok, api_key_configured, model, base_url}` |
| GET | `/api/grades` | 支持的年级列表 |
| GET | `/api/demo-samples` | 可用的演示快捷入口（自动扫 `data/demo_reports/`） |
| POST | `/api/evaluate` | 同步评测（30-90s） |
| GET | `/api/reports` | 历史报告列表 |
| GET | `/api/reports/{id}` | 单条历史报告 |
| GET | `/` | 单页前端 |

`POST /api/evaluate` 请求体：
```json
{
  "text": "课件文本…",
  "grade": "九年级",
  "source": "live",         // 或 "demo:01_good_二次函数"
  "dual_sample": true,
  "dual_threshold": 1
}
```

## 5. 目录结构

```
src/edu_eval/api/        # FastAPI 应用
  ├── __init__.py
  ├── main.py            # 应用实例 fastapi_app（避开与模块名冲突）
  ├── storage.py         # SQLite 历史持久化（results/demo.db）
  ├── models.py          # Pydantic 模型
  └── routes/
      ├── health.py      # /api/health, /api/grades, /api/demo-samples
      ├── evaluate.py    # /api/evaluate
      └── history.py     # /api/reports, /api/reports/{id}

static/                  # 前端单页（纯 HTML+CSS+JS+Chart.js）
  ├── index.html
  ├── style.css
  └── app.js

data/samples/demo/       # 演示样本 markdown（3 个）
data/demo_reports/       # 预生成的演示报告 JSON（pregen_demo_reports.py 输出）

results/
  ├── demo.db            # SQLite 历史（gitignored）
  └── demo.log           # 启动日志
```

## 7. 故障排查

| 现象 | 排查 |
|---|---|
| `/api/health` 返回 `api_key_configured: false` | `.env` 未被 source，或未 export `HY3_API_KEY` |
| 评测返回 500 + `RateLimitError` | OpenRouter 免费层日额度耗尽，等明早重置，或换模型 |
| 演示按钮点击 404 | `data/demo_reports/` 下无对应文件，先跑 `pregen_demo_reports.py` |
| 静态文件 404 | 检查仓库根目录 `static/` 是否存在 |
| 端口占用 | `lsof -i:8000` 找占用，杀掉或换端口启动 |
