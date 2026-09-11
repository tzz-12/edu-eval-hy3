# EduEval Demo · 部署与启动

把 EduEval 评测器打包成一个 FastAPI + 单页前端的应用，本地一键启动。

## 1. 准备环境

仓库根目录的 `.env`（gitignored）需配置：

```ini
HY3_BASE_URL=https://你的-Hy3-端点/v1
HY3_API_KEY=你的密钥
HY3_MODEL=hy3
HY3_MAX_TOKENS=16384
HY3_TIMEOUT=300
```

依赖：Python 3.10+、FastAPI 0.141、uvicorn 0.52、pymupdf（解析 PDF）。

## 2. 一键启动

```bash
bash scripts/run_demo.sh start        # 默认 8000 端口，mock 模式
bash scripts/run_demo.sh start 8765   # 自定义端口
bash scripts/run_demo.sh start 8000 --live   # 真实调 API（需 .env 已配 key）
bash scripts/run_demo.sh pregen       # 重生成演示报告（可加 --live）
bash scripts/run_demo.sh status
bash scripts/run_demo.sh tail         # 看日志
bash scripts/run_demo.sh stop
```

启动后浏览器打开 `http://localhost:8000`。

不加 `--live` 时走内置 mock 后端（`HY3_MOCK=1`），不通网、不耗额度，
用于验证前后端链路贯通；要跑真实评测必须显式加 `--live`。

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

> 预生成 3 份报告在双采样模式下约消耗 80~120 次 Hy3 调用，单份耗时约 2~25 分钟。
> 已生成的报告可重复使用，不会再次消耗额度；改了哪份就跑哪份：
> `python scripts/pregen_demo_reports.py 01`。

## 4. API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 不暴露 key，返回 `{ok, api_key_configured, model, base_url, db_writable, db_path, db_note, db_count}` |
| GET | `/api/grades` | 支持的年级列表 |
| GET | `/api/demo-samples` | 可用的演示快捷入口（自动扫 `data/demo_reports/`） |
| POST | `/api/evaluate` | 同步评测（30-90s） |
| GET | `/api/reports` | 历史报告列表 |
| GET | `/api/reports/{id}` | 单条历史报告 |
| GET | `/api/manual` | 评测说明数据：维度口径 / 权重 / 分档阈值 / 参数，实时读 `eval/dimensions.py`（不手抄，避免文档与实现漂移）|
| GET | `/` | 单页前端 |

`POST /api/evaluate` 请求体：
```json
{
  "text": "课件文本…",
  "grade": "九年级",
  "file_name": "选填，上传文件时传原名",
  "source": "live",         // 或 "demo:01_good_二次函数"
  "dual_sample": true,
  "dual_threshold": 1
}
```

- `source` 以 `demo:` 开头时 `text` 可省略，直接读预生成报告，**不写历史库**（避免污染）。
- `file_name` 留空时，历史列表显示名会取正文首个 `# 一级标题`，再兜底为「粘贴文本」——
  不会退化成正文前 N 字把表格糊满。
- `demo:` 后面的 id 含 `/` 或 `..` 一律 400（防路径穿越）。

## 5. 历史库落点与降级

`storage.py` 在 import 时按序探测并选第一个可写的路径：

1. 环境变量 `EDU_EVAL_DEMO_DB`
2. `./results/demo.db`（仓库内，gitignored）
3. 系统临时目录 `<tmpdir>/edu_eval_demo/demo.db`

受限环境（如沙箱拦截仓库目录写入）下会自动降级到临时目录，历史功能照常可用；
`/api/health` 的 `db_path` / `db_note` 会暴露实际落点，前端顶栏也会显示「历史库已降级」。
所有 DB 操作出错时降级为返回空值，**不会把 500 抛给前端**。

## 6. 目录结构

```
src/edu_eval/api/        # FastAPI 应用
  ├── __init__.py
  ├── main.py            # 应用实例 fastapi_app（避开与模块名冲突）
  ├── storage.py         # SQLite 历史：路径探测降级 + 连接不泄漏 + 错误降级
  ├── serialize.py       # sympy 等非原生类型的 JSON 清洗（API 与 pregen 共用）
  ├── dirs.py            # 共享路径常量（演示报告目录）
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
| 评测返回 500 + `RateLimitError` / 402 | 端点额度耗尽或未开通后付费；换端点或等额度恢复 |
| 演示按钮点击 404 | `data/demo_reports/` 下无对应文件，先跑 `pregen_demo_reports.py` |
| `/api/reports` 500 + `disk I/O error` | 历史库路径不可写；`/api/health` 查 `db_path`/`db_note`，必要时用 `EDU_EVAL_DEMO_DB` 指定可写目录 |
| 顶栏显示「历史库已降级」 | `results/` 不可写，已自动落到临时目录；评测本身不受影响，只是历史不持久 |
| 静态文件 404 | 检查仓库根目录 `static/` 是否存在 |
| 启动报 `ModuleNotFoundError: No module named 'edu_eval'` | PYTHONPATH 未含 `src/`；`run_demo.sh` 已处理，手动启动需 `export PYTHONPATH=$PWD/src` |
| 端口占用 | `lsof -i:8000` 找占用，杀掉或换端口启动 |
