# EduEval · 基于混元（Hy3）的初中数学教学设计质量评估器

> ⚠️ **声明（务必阅读）**：本仓库为腾讯犀牛鸟开源实战「混元大语言模型」项目的**个人 / 活动作品**，**并非腾讯官方发布**，不代表腾讯公司立场。项目仅通过 API 调用混元（Hy3）提供的模型能力，**不训练、不微调任何模型，不做本地推理部署**。
>
> 📮 **提交方式**：本课题产出为一个**独立应用仓库**。按活动 issue
> [Tencent-Hunyuan/Hy3#4](https://github.com/Tencent-Hunyuan/Hy3/issues/4) 的「完成方式」，
> 以 Pull Request 形式向活动专用分支 [`rhinobird2026`](https://github.com/Tencent-Hunyuan/Hy3/tree/rhinobird2026)
> 提交**本项目的说明与仓库链接**——PR 只承载项目材料，**不改动 Hy3 模型仓库自身的代码**。

---

## 1. 项目介绍

EduEval 是一个用于评估 **AI 生成的初中数学单课时教学设计** 质量的轻量评估器。它把
“教得好”这种高度主观、无唯一标准的质量，转化为一套**可操作、可复核、可抵御表面包装**的判定。

核心设计（详见方案文档，本仓库为可运行实现）：

- **知识正确性准入优先（G0）**：先用本地知识库检索 + 程序计算 + 事实 Judge 核验概念、公式、
  适用条件与答案；任一确认错误即 `FAIL`，证据不足即 `NE`，只有 `PASS` 才进入后续评分。
- **多 Judge 协同（均基于 Hy3）**：事实 Judge、教学设计 Judge、表达与安全 Judge 通过不同角色
  提示实现专业分工；复核 Judge 检查证据完整性，必要时触发仲裁。
- **1 准入 + 8 加权维度 + 1 辅助维度**（权重合计 100%）：教学目标、学段适配、教—学—评一致性、
  教学环节、学情分析、学习者中心与启发探究、表述清晰度、安全合规（含红线）、格式可读性。
- **声明 vs 识别**：用户声明目标年级 / 版本 / 课题 / 课时，评估器识别实际难度与知识范围并比对，
  差异即超纲 / 错配信号（维度 3）。
- **不引入人工标注**：评测器工作流全自动，教师标注只作为可选外部对照，不进入日常流程。

### 1.1 Hy3 在本项目中承担的角色

EduEval 是「确定性层 + Hy3 语义层」的双层结构。划分原则只有一条：**能被程序验证的绝不交给模型，需要读懂教学意图的才交给 Hy3。**

| 层次 | 承担者 | 具体职责 |
|---|---|---|
| 确定性层（零 LLM） | 本地代码 | 多格式解析、本地知识库三层检索、sympy 公式恒等验算、概念→年级映射查表、章节结构完整性检查、维度加权聚合与准入决议 |
| 语义层 | **Hy3 API** | 事实核验、教学设计判断、表达与安全审读、证据复核与仲裁——所有「需要读懂教学意图」的判定 |
| 稳定性层 | **Hy3 API** | 双采样自一致：同一提示独立采样两次，两视角分歧超过阈值时交由 Hy3 仲裁（见 §6 总评规则） |

调用形态上，Hy3 以 **5 类角色提示**（fact / design / expression_safety / review / arbitrate）承载 10 个维度的判定；一次完整评测在双采样开启时通常产生 **8~20 次** Hy3 调用（重复采样 + 必要时仲裁）。

**Hy3 不负责的部分**：解析文件、查检索、算公式、算加权总分——这些走确定性代码，保证同输入同输出、可复算。

> 本仓库评测结果全部由 **Hy3 API** 产生。换用其他模型端点等同于更换裁判，判别力 / 稳定性数据不可跨模型比较（配置方式见 §4）。

支持格式：`.md` / `.txt` / `.docx` / `.pdf` / `.pptx`（PDF、PPTX 依赖可选依赖，缺失时友好降级）。

> 📄 **方案文档（设计说明）**：完整的选题背景、问题定义、维度构建依据与评分锚点、评测方法、
> 预期效果与时间规划见本仓库 [`DESIGN.md`](./DESIGN.md)。

## 2. 环境要求

- Python ≥ 3.10
- 可访问的 **Hy3 OpenAI 兼容端点**与 **API Key**（通过环境变量传入）
- 操作系统：Windows / macOS / Linux

## 3. 安装

```bash
git clone <本仓库地址>
cd edu-eval-hy3
pip install -r requirements.txt
```

## 4. 配置（密钥只走环境变量，绝不硬编码 / 提交）

复制模板并填入你的 Hy3 端点与 Key：

```bash
cp .env.example .env
# 编辑 .env：
#   HY3_BASE_URL=https://你的-Hy3-端点/v1
#   HY3_API_KEY=你的密钥
#   HY3_MODEL=hy3
```

> 本项目所有密钥均来自环境变量；`.env` 已被 `.gitignore` 忽略，不会进入版本库。
> 你也可以直接 `export HY3_BASE_URL=... HY3_API_KEY=...` 而不使用 `.env` 文件。

## 5. 运行方式

### 5.1 命令行（CLI）

```bash
# 基本用法
python -m edu_eval data/samples/example_lesson.md \
    --grade 七年级 --version 人教版 --topic 一元一次方程 --period 1课时

# 指定本地知识库、输出 JSON、写入文件
python -m edu_eval 教案.docx --kb data/knowledge_base/sample_kb.jsonl --json --out report.json
```

演示模式（不连接 Hy3，返回占位结果，用于验证流程贯通）：

```bash
HY3_MOCK=1 python -m edu_eval data/samples/example_lesson.md
```

### 5.2 Web 应用（推荐演示方式）

```bash
bash scripts/run_demo.sh start 8000 --live   # 真实调用 Hy3；不加 --live 走 mock，不联网不耗额度
bash scripts/run_demo.sh status              # 查看进程与健康状态
bash scripts/run_demo.sh tail                # 跟踪日志
bash scripts/run_demo.sh stop                # 停止
```

浏览器打开 `http://127.0.0.1:8000`：左栏历史记录、右栏对话流；上传课件或直接粘贴文本并声明年级即可评测。
报告以 5 个标签页呈现（总览 / 维度详情 / 一致性 / 规则层 / 改进建议），内嵌雷达图与图表。
右上角 `?` 打开「评测说明」面板，可查 10 个维度的口径、1·3·5 分锚点、参数含义与结果解读。

还有一个更轻的单文件版本（后端直接渲染 HTML 报告，无前端依赖）：

```bash
python -m edu_eval.web
```

首次启动前若 `data/demo_reports/` 为空，首页的演示样本按钮不可用，先执行
`bash scripts/run_demo.sh pregen --live` 生成预生成报告（约 2~25 分钟/份，见 `docs/demo.md`）。

> 🎬 演示视频的分镜脚本、录制与导出方法见 [`docs/demo_video.md`](./docs/demo_video.md)。

### 5.3 知识库重建

`data/kb/` 因许可原因不入库（K12-KGraph 为 CC BY-NC-SA 4.0），克隆后需本地重建：

```bash
export PYTHONPATH=src
# 数据来源与获取方式见 docs/sources_inventory.md
python -m edu_eval.kb.ingest      # 导入初中概念 → data/kb/knowledge.{jsonl,db}
python -m edu_eval.kb.grade_map   # 概念→年级映射 → data/kb/concept_grade.json
python -m edu_eval.kb.curriculum  # 课标结构化 → data/kb/curriculum_junior.jsonl
```

### 5.4 评测资产生成

```bash
export PYTHONPATH=src
# 缺陷样本（注入后自动跑规则层做检出闭环验证）
python scripts/inject_defects.py --topic 一元一次方程 --grade 七年级
# 检索测试集 + 知识库覆盖缺口清单
python scripts/build_retrieval_testset.py
```

### 5.5 运行测试

```bash
HY3_MOCK=1 PYTHONPATH=src python -m pytest tests/ -v
```

未构建知识库时，依赖知识库的测试自动跳过（`skip`），其余照常运行。

## 6. 评估维度一览

| 优先级 | # | 维度 | 权重 |
|---|---|---|---|
| G0 准入 | 2 | 知识绝对正确性（任一错误即 FAIL） | 不计权重 |
| P0 底线 | 6 | 安全合规与价值导向（含红线） | 5% |
| P1 核心 | 1 | 教学目标明确性与课标对齐 | 20% |
| P1 核心 | 3 | 学段与认知层次适配 | 15% |
| P1 核心 | 8 | 教—学—评一致性 | 20% |
| P2 教学 | 4 | 教学环节设计合理性 | 15% |
| P2 教学 | 7 | 学情分析 | 10% |
| P2 教学 | 9 | 学习者中心与启发探究 | 10% |
| P3 可用 | 5 | 表述清晰度与易懂性 | 5% |
| 辅助 | A | 格式与基本可读性 | 不计分 |

总评规则：先执行知识准入，`FAIL`→不通过、`NE`→暂不可评；通过后如安全红线成立仍判不通过；
否则按 8 个加权维度计算 `Σ(维度得分/5 × 权重)`，85+ 优秀、70+ 良好、60+ 合格、<60 待改进。
辅助维度 A 不计入总分。

## 7. 项目结构

```
edu-eval-hy3/
├── README.md
├── DESIGN.md               # 方案定稿（8 维度 / G0 准入 / 评测方法）
├── IMPLEMENTATION_PLAN.md  # 落地计划（Phase 0/1 分离、里程碑）
├── .env.example            # 密钥模板（.env 已被忽略）
├── requirements.txt
├── src/edu_eval/
│   ├── config.py           # 仅从环境变量读取配置
│   ├── hy3.py              # Hy3 OpenAI 兼容客户端封装（含 mock 模式）
│   ├── cli.py              # 命令行入口
│   ├── web.py              # 轻量 Web 应用：后端渲染 HTML 报告
│   ├── api/                # FastAPI 演示应用（对话式前端 + SQLite 历史，见 docs/demo.md）
│   ├── parse/
│   │   ├── parsers.py      # 多格式解析（md/txt/docx/pdf/pptx）
│   │   └── layout.py       # 公式标记与章节层级归一
│   ├── kb/                 # 知识库层
│   │   ├── schema.py       # Tier1/Tier2 条目 schema + 来源元数据校验
│   │   ├── sources.yaml    # 四个来源的许可与约束清单
│   │   ├── ingest.py       # K12-KGraph 导入 + FTS5 索引
│   │   ├── retriever.py    # 三层检索（精确 / 长度加权子串 / FTS 兜底）
│   │   ├── grade_map.py    # 概念→年级确定性映射（维度 3 查表）
│   │   ├── curriculum.py   # 课标 2022 OCR → 结构化条目
│   │   └── grade_exempt.json  # 图谱收录偏差豁免表（逐条附教材依据）
│   └── eval/
│       ├── dimensions.py   # 维度定义（与方案 §4.2 一致）
│       ├── rules.py        # 确定性规则层（零 LLM）：公式/年级/结构
│       ├── orchestrator.py # 多 Judge 编排（规则层前置、复核、仲裁）
│       ├── judges/         # fact / design / expression_safety / review / arbitrate
│       ├── cache.py        # 磁盘缓存（键含样本+角色+提示版本+规则证据）
│       ├── rubric.py       # 量规访问层（分数档位、雷达图 SVG）
│       ├── aggregator.py   # 确定性聚合（权重/红线/准入）
│       └── run_eval.py     # 评估主流程（委托编排器）
├── static/                 # 单页前端（index.html / style.css / app.js / Chart.js 本地副本）
├── scripts/
│   ├── run_demo.sh              # 一键启停演示应用（start / stop / status / tail / pregen）
│   ├── pregen_demo_reports.py   # 预生成演示报告（支持按样本前缀重跑）
│   ├── inject_defects.py        # 缺陷注入器 + 规则层检出闭环验证
│   ├── build_retrieval_testset.py  # 检索测试集 + 知识库缺口清单
│   ├── run_discrimination.py    # 判别力实验（base / mild / severe 三档）
│   └── run_stability.py         # 稳定性实验（同一文档重测一致性 MAD）
├── data/
│   ├── samples/            # 示例教学设计、注入缺陷样本
│   ├── testsets/           # 检索测试集、覆盖缺口清单（入库）
│   └── kb/                 # 知识库衍生产物（不入库，见 §5.3 重建）
├── docs/
│   ├── sources_inventory.md      # 资料来源与许可清单
│   ├── kb_scope.md               # 知识库范围、已知局限与实测覆盖率
│   ├── dimension_benchmarking.md # 与 EQuIP / Danielson FFT / 教育部优课量表的逐维对照
│   └── demo.md                   # 演示应用部署、API 端点与故障排查
└── tests/                  # 282 个测试（1 个因缺本地知识库而 skip）
```

## 8. 能力边界（诚实说明）

- 本项目的知识准入依赖于**本地知识库的覆盖度**；知识库未覆盖或来源冲突的核心断言会返回 `NE`，
  不臆断为正确。
- 评估器不宣称等同于教师专家判断；结论限定为：知识条目可追溯、覆盖范围内的已知错误不会被
  其他维度高分抵消、教学缺陷具备可测判别力、在重复运行中保持基本稳定。
- PDF / PPTX / 图片的解析质量会影响评估，复杂版面或公式识别失真时可能标记 `NE`，属解析层误差。
- 规则层只覆盖**确定性缺陷**（公式恒等错误、年级越界、结构完整性），**语义层缺陷**
  （目标空泛、启发引导缺失、伪启发包装）必须由 LLM Judge 判定。详见下方实测数据。

### 8.1 当前实测数据（非目标值）

| 项目 | 实测值 | 说明 |
|---|---|---|
| 概念→年级映射覆盖率 | 451/451 = 100% | 沿图谱 `appears_in`/`is_part_of` 边推导 · 与裁判模型无关 |
| 13 课题核心概念覆盖 | 31/40 = **77.5%** | 缺失集中在方法类概念，清单见 `data/testsets/kb_gaps.json` · 与裁判模型无关 |
| 检索 top3 命中率 | name/alias 100%、context 90% | 138 条测试集自动评测 · 与裁判模型无关 |
| 检索零召回率（干扰项） | 81% | 高中术语不误报为初中概念 · 与裁判模型无关 |
| 规则层缺陷检出 | 10 条注入样本 → FAIL 2 / WARN 1 / 未检出 7 | 未检出的 7 条为语义层缺陷，按设计交由 Judge |
| 判别力（base→mild→severe） | D1 5→3→1、D4 5→2→2、D7 5→2→1、D8 5→3→2、D9 5→2→2 | 注入缺陷随档位下降 · `--repeat 3` 取众数 · **依赖裁判模型** |
| 稳定性（重测一致性） | MAD = 0（阈值 ≤0.5） | 同一底稿重复运行 5 次 · **依赖裁判模型** |

上述口径与生成方式见 `docs/kb_scope.md` §5，均可一键复算。

### 8.2 关于裁判模型（换模型 = 换裁判）

上表标「依赖裁判模型」的两行由**裁判模型**跑出，**不可跨模型沿用**。项目开发期曾在 Hy3 端点尚未
开通时用其他 OpenAI 兼容端点作为临时裁判跑通机制与实验流程；**正式提交版本的裁判与演示报告均由
Hy3 API 产生**。需要用 Hy3 复现这两组实验时，直接跑：

```bash
export PYTHONPATH=src
python scripts/run_discrimination.py --dims 1,4,7,8,9 --repeat 3 --out results/discrimination_hy3.json
python scripts/run_stability.py -n 5
```

### 8.3 当前状态

Phase 0（不依赖模型的部分：多格式解析、知识库、规则层、聚合器）与 Phase 1
（判别力 / 一致性 / 解析健壮性三项实验）均已完成，全量测试 `282 passed, 1 skipped`。
当前推进最后一环：演示应用、评测说明与交付文档。

## 9. 许可与归属

- 代码以 [MIT License](./LICENSE) 开源。
- 知识库示例条目来自公开的课程标准与教材内容，仅作演示；构建正式知识库时请遵守对应来源许可。
- **本仓库为个人 / 活动作品，非腾讯官方发布。**

## 10. AI 协作说明

本项目的开发全程使用腾讯 **WorkBuddy** 作为 AI 编码助手（「开源课题实战」阶段学员可获 WorkBuddy / CodeBuddy 定量 token 额度）。按活动建议，此处记录协作范围：

| 环节 | 主要承担方 |
|---|---|
| 选题、问题定义、五类质量问题的形式化 | 作者 |
| 评测维度设计、权重与 1·3·5 分锚点、红线规则 | 作者（并逐维对照 EQuIP / Danielson FFT / 教育部优课量表，见 `docs/dimension_benchmarking.md`）|
| 知识库来源取舍、许可核对、覆盖缺口判定 | 作者 |
| 实验判读（判别力是否达标、稳定性是否可接受）与结论 | 作者 |
| 代码实现：多格式解析、知识库三层检索、规则层、多 Judge 编排、聚合器、双采样自一致 | WorkBuddy 协作完成 |
| 演示应用：FastAPI 后端、SQLite 历史、单页对话式前端与内嵌图表、评测说明面板 | WorkBuddy 协作完成 |
| 实验脚本与回归测试（`scripts/` 全套 + `tests/` 282 条） | WorkBuddy 协作完成 |
| 文档整理：README、`docs/`、方案文档的章节重组与排版 | WorkBuddy 协作完成 |

> 本表按**模块**说明协作范围，不做逐行标注；所有改动经作者审阅后才进入版本库。
> 口径、标准与结论由作者负责，AI 负责实现与表达。

---

*EduEval · 基于混元 Hy3 · 腾讯犀牛鸟开源实战「混元大语言模型」项目个人作品*
