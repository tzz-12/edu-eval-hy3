# EduEval · 基于混元（Hy3）的初中数学教学设计质量评估器

> ⚠️ **声明（务必阅读）**：本仓库为腾讯犀牛鸟开源实战「混元大语言模型」项目的**个人 / 活动作品**，**并非腾讯官方发布**，不代表腾讯公司立场。项目仅调用混元（Hy3）提供的模型能力，**不训练、不微调任何模型**，也**不向 Hy3 官方仓库提交 Pull Request**。

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
#   HY3_MODEL=hunyuan-turbo
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

### 5.2 Web 应用（可选）

```bash
python -m edu_eval.web
# 浏览器打开 http://127.0.0.1:8000 ，上传文件并填写声明元数据即可评估
```

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
│   ├── web.py              # Web 应用：HTML 报告（维度卡片+证据+雷达图）
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
├── scripts/
│   ├── inject_defects.py   # 缺陷注入器 + 规则层检出闭环验证
│   └── build_retrieval_testset.py  # 检索测试集 + 知识库缺口清单
├── data/
│   ├── samples/            # 示例教学设计、注入缺陷样本
│   ├── testsets/           # 检索测试集、覆盖缺口清单（入库）
│   └── kb/                 # 知识库衍生产物（不入库，见 §5.3 重建）
├── docs/
│   ├── sources_inventory.md  # 资料来源与许可清单
│   └── kb_scope.md           # 知识库范围、已知局限与实测覆盖率
└── tests/                  # 56 个测试
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
| 概念→年级映射覆盖率 | 451/451 = 100% | 沿图谱 `appears_in`/`is_part_of` 边推导 |
| 13 课题核心概念覆盖 | 31/40 = **77.5%** | 缺失集中在方法类概念，缺口清单见 `data/testsets/kb_gaps.json` |
| 检索 top3 命中率 | name/alias 100%、context 90% | 138 条测试集自动评测 |
| 检索零召回率（干扰项） | 81% | 高中术语不误报为初中概念 |
| 规则层缺陷检出 | 10 条注入样本 → FAIL 2 / WARN 1 / 未检出 7 | 未检出的 7 条为语义层缺陷，按设计交由 LLM Judge |

上述口径与生成方式见 `docs/kb_scope.md` §5，均可一键复算。

### 8.2 当前状态

Phase 0（不依赖 Hy3 Key 的部分）已完成，M1/M2 里程碑达成。
Phase 1（样本生成、Judge 实测、判别力/一致性/对抗性三项实验）需要 Hy3 接入信息后开展。

## 9. 许可与归属

- 代码以 [MIT License](./LICENSE) 开源。
- 知识库示例条目来自公开的课程标准与教材内容，仅作演示；构建正式知识库时请遵守对应来源许可。
- **本仓库为个人 / 活动作品，非腾讯官方发布。**

---

*EduEval · 基于混元 Hy3 · 腾讯犀牛鸟开源实战「混元大语言模型」项目个人作品*
