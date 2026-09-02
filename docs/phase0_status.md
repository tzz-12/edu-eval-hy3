# Phase 0 验收报告

> 本文逐条核对 `IMPLEMENTATION_PLAN.md` §2「Phase 0」的 9 项任务与 2 个里程碑，
> 列出**实测数据**（而非计划值），并记录复盘中发现的 3 个遗留缺陷（P0-10）。
> 复测环境：macOS / Python 3.13.12 / 仓库根目录 / `HY3_MOCK=1`。

---

## 0. 总览

| 项 | 结果 |
|---|---|
| P0-1 ~ P0-9 | **9/9 完成** |
| 里程碑 M1（规则层零 LLM 判出错误公式） | **达成** |
| 里程碑 M2（mock 模式完整报告流程） | **达成** |
| 测试 | `56 passed` |
| 代码规模 | `src` + `scripts` + `tests` 共 4,439 行 |
| 提交 | 8 commits，已推送 `main` |
| 遗留缺陷 | **3 个（其中 1 个影响 Phase 1 数据质量，建议立即修）** |

---

## 1. 逐项核对

### P0-1 知识库 schema + 来源清单 ✅

| 产出 | 行数 | 说明 |
|---|---|---|
| `src/edu_eval/kb/schema.py` | 156 | `Tier1Entry` / `Tier2Entry` 双类型；`source`/`license`/`version` 必填校验；`dump_jsonl` 自动建目录 |
| `src/edu_eval/kb/sources.yaml` | — | 四来源许可清单（K12-KGraph / 课标 2022 / 部级精品课指标 / 教材目录） |
| `docs/kb_scope.md` | — | 13 课题范围、Tier2 断言预算、§5 已知局限 |

**完成标志**：schema 双类型 ✅；必填字段校验 ✅（缺失即抛错，不静默通过）。

---

### P0-2 K12-KGraph 导入 + FTS5 索引 ✅

**实测数据**（`data/kb/knowledge.jsonl`，451 条）

| 字段 | 填充 | 占比 |
|---|---:|---:|
| 条目总数 | 451 | — |
| `definition` | 451 | **100%** |
| `grade` | 451 | **100%** |
| `aliases` | 54 | 12.0% |
| `prerequisites` | 150 | 33.3% |

**检索质量基线**（`data/testsets/retrieval_queries.jsonl`，138 条；
分母只计「有期望答案」的条目——无期望者为知识库缺口，计入会稀释指标、掩盖真实质量问题）

| 类别 | n | top1 | top3 | 测试基线 |
|---|---:|---:|---:|---|
| name（概念名） | 51 | **100%** | **100%** | ≥95% |
| alias（别名） | 20 | **100%** | **100%** | ≥95% |
| context（句中召回） | 21 | **71%** | **90%** | ≥80% |
| distractor（无关查询零召回） | 26 | — | **81%** | ≥70% |

**过程中修掉的两个真实缺陷（均已回归测试锁定）**

1. **单字概念劫持 top1**：早期 `contains` 匹配给固定 60 分，导致「抛物线」→「线」、
   「余弦」→「弦」、「一元二次方程 x²-4x+3=0」→「0」。改为按匹配长度加权 + 多字查询过滤单字。
2. **FTS5 bm25 方向反转**：`bm25()` 越负越相关，早期按分数降序排 → 最相关的排最后
   （「积分」召回「积的乘方」）。改为单调递增映射并加阈值。

**遗留**：`aliases` 仅 12%，是 context 类 top1（71%）的主要瓶颈 → 由 P1-2 定向补充。

---

### P0-3 概念 → 年级映射表 ✅

| 指标 | 实测 | 要求 |
|---|---|---|
| 可映射年级册次 | **451 / 451（100%）** | 100% |
| 单条查询耗时 | 1000 次查询 **0.04 ms**（单条约 0.00004 ms） | < 10 ms |

推导路径：`Concept →(appears_in)→ Section →(is_part_of)→ Chapter →(is_part_of)→ Book`

册次分布（概念跨册次会重复计数）：

| 册次 | 概念数 |
|---|---:|
| 七年级上册 | 80 |
| 七年级下册 | 94 |
| 八年级上册 | 80 |
| 八年级下册 | 76 |
| 九年级上册 | 86 |
| 九年级下册 | 46 |

**修掉的真实缺陷**：「代数式」在图谱中挂在八年级下册，但人教版实为七年级上册引入，
导致七年级教案出现**系统性超纲假阳性**。已建 `src/edu_eval/kb/grade_exempt.json`
豁免表，规则层输出 `R-GRADE-EXEMPT` 留痕供审计。

豁免表结构（`grade_exempt.json`，顶层 6 个键中有 4 个是元信息 `_schema`/`_why`/`_usage`/`_audit`）：

| 键 | 含义 | 条目数 |
|---|---|---:|
| `exempt` | 确认图谱有误、**予以豁免**的概念 | **1**（代数式：图谱八下 → 实际七上引入） |
| `_reviewed_keep` | 已核查确认**归属正确、不得豁免**的概念 | **2**（求根公式、一元二次方程，均属九上，是有效超纲判据） |

> 即：实际概念条目只有 **3 条**（1 条豁免 + 2 条核实保留）。
> 这是「逐个人工审核」的清单，不是自动生成的——每扩一条都要附教材依据，所以它天然很小。

---

### P0-4 课标文本结构化 ✅

`data/kb/curriculum_junior.jsonl`，**160 条**，每条含 `page`（印刷页码 = PDF 页 + 7）与 `std_id`。

| 维度 | 分布 |
|---|---|
| 领域 | 数与代数 59 / 图形与几何 85 / 统计与概率 11 / 综合与实践 5 |
| 栏目 | 内容要求 141 / 学业要求 15 / 教学提示 4 |

**已知限制**：OCR 文本中公式被扭曲（`(a+b)(a-b)=a²-b²` → 「（a+B）（a一B）=Q' B」），
**课标条目只取文字，不取公式**——公式来源只能是 Tier2 断言集。

---

### P0-5 规则层（确定性校验） ✅ —— M1 里程碑

`src/edu_eval/eval/rules.py`（389 行），三类规则：

| 规则 | 判定 | 实测样例 |
|---|---|---|
| `R-FORMULA` | sympy 符号恒等 + 数值采样反例（种子 42，可复现） | `(a+b)²=a²+b²` → **fail**，反例 `{a:4, b:1, lhs:25, rhs:17}` |
| | | `(a+b)(a-b)=a²-b²` → pass（恒等式） |
| | | `2x+3=11` → **ne**（两侧符号集不同 = 方程，非公式断言，交 Judge） |
| `R-GRADE-EXPL` | 文本显式出现的概念越界 → **fail** | 七年级设计提「一元二次方程」「求根公式」（均属九年级上册）→ fail |
| `R-GRADE-ASSOC` | 仅由检索联想到的越界 → warn | 区分显式/联想，避免联想误杀 |
| `R-GRADE-EXEMPT` | 豁免留痕 | 见 P0-3 |
| `R-STRUCT` | 结构完整性 | 缺「教学过程」章节 → warn |

**M1 验收**：一条含 `(a+b)²=a²+b²` 的七年级教案，规则层判定 `g0_rule_verdict = FAIL`，
**全程零 LLM 调用**，直接一票否决。✅

**设计边界**：规则层只判**确定性**缺陷（公式恒等 / 年级越界 / 结构完整性），
语义类缺陷（术语错误、缺启发引导等）按设计留给 LLM Judge。

---

### P0-6 多格式解析升级 ✅

5 格式实测（各造一份最小样例）：

| 格式 | `parse_status` | `parse_confidence` | 页数 |
|---|---|---:|---:|
| `.md` | ok | 0.98 | — |
| `.txt` | ok | 0.98 | — |
| `.docx` | ok | 0.95 | 1 |
| `.pdf` | ok | 0.92 | 1 |
| `.pptx` | ok | 0.90 | 1 |
| `.pdf`（空扫描件） | **`ocr_required`** | 0.10 | 1 |

空扫描件 `notes`：`PDF 未抽取到文本：图片型扫描件，需要 OCR 后再评估。`
编排层约定：`ocr_required` → 直接 `NE`，不猜测、不进入评分。

---

### P0-7 Judge 拆分 + 编排 + 缓存 ✅

5 类 Judge（`src/edu_eval/eval/judges/`，共 308 行）：`fact` / `design` / `expression_safety` /
`review` / `arbitrate`。

编排顺序（`orchestrator.py`，230 行）：

```
解析 → [ocr_required ? NE] → 规则层(零 LLM)
     → [规则层 FAIL ? 一票否决] → fact Judge(G0 + 维度3)
     → design + expression_safety Judge → review 复核 → 争议维度仲裁 → 聚合
```

- **缓存**：按「样本 hash + 角色 + 提示版本 + 规则层证据」生成 key，落盘 `results/.cache/judge/`（现 5 条）。
- **提示版本**：`PROMPT_VERSION` 常量，写入 `_meta.prompt_version`；改提示即整体失效重跑。
- **关键修复**：规则层 findings 原先没传给 Judge（维度 3 完全依赖 LLM，浪费已算出的硬结论）。
  现以 `rule_evidence` 注入 fact Judge，并**纳入 cache_key** 防止不同样本串味。

---

### P0-8 聚合 + Web 可视化 ✅ —— M2 里程碑

- `dimensions.py`：9 个评分维度 + 1 辅助。权重合计 100%：
  目标 20 / 一致性 20 / 学段适配 15 / 环节设计 15 / 学情 10 / 启发探究 10 / 表达 5 / 安全 5；
  维度 2（知识正确性）为 G0 准入不占权重；维度 6 为红线。
- `rubric.py`（99 行）+ `aggregator.py`（62 行）：加权总分、等级、红线处理。
- `web.py`（192 行）：上传 → 准入 → 8 维 → HTML 报告（维度卡片 + SVG 雷达图 + 证据引用），
  另有 `/api/evaluate` 返回 JSON。

**M2 验收**：mock 模式上传样例 → HTML 含 `<svg>` 雷达图、维度卡片、知识库命中信息；
规则层硬错误样本能渲染「规则层确定性检查」区块。✅

---

### P0-9 缺陷注入器 + 检索测试集 ✅

**缺陷注入器**（`scripts/inject_defects.py`，302 行）：11 类缺陷，每条记录
`sample_id / type / severity / 期望准入 / 期望受影响维度 / position / injected / text / 规则层实际判定`。

| 缺陷类型 | 严重度 | 规则层实测 | 期望影响维度 |
|---|---|---|---|
| formula_wrong | critical | **FAIL（新增）** | 2 |
| formula_wrong | critical | — | 2 |
| formula_wrong | critical | 未检出 | 2 |
| term_wrong | critical | 未检出 | 2 |
| grade_beyond | major | **FAIL（新增）** | 3 |
| no_goals | major | 未检出 | 1 |
| no_structure | moderate | **WARN（新增）** | 4 |
| no_heuristic | major | 未检出 | 9 |
| no_learner | moderate | 未检出 | 7 |
| no_assess | major | 未检出 | 8 |
| pseudo_quality | adversarial | 未检出 | 1, 4, 9 |

> 11 条中规则层检出 3 条（2 FAIL + 1 WARN），其余 8 条是**语义类缺陷**，
> 按设计留给 LLM Judge —— 这不是漏检，而是规则层的能力边界。
> Phase 1 要用这 11 条做「规则层 vs Judge」的能力对比。

**检索测试集**：138 条（≥100 ✅），四类齐全，期望答案**独立于检索器**查表生成（避免循环论证）。

**知识库缺口**（`data/testsets/kb_gaps.json`）：13 课题手工列的 40 个核心概念，
图谱命中 31 = **77.5%**；缺失 9 个全是方法类/性质类概念。这是 P1-1 的直接输入。

> ⚠️ 注意区分两个覆盖率口径，不要混用：
> **100%** = 图谱 451 个概念的年级映射覆盖率；
> **77.5%** = 13 课题 40 个核心概念在图谱中的命中率。

---

## 2. 复盘发现的 3 个遗留缺陷（P0-10 待修）

### 缺陷 A（严重）知识库装载静默失败 —— fact Judge 拿不到任何知识库上下文

`KnowledgeBase.load()` 对真实 `knowledge.jsonl` 抛
`TypeError: KBEntry.__init__() got an unexpected keyword argument 'name'`，
而 `orchestrator.load_kb_assets()` 用 `except Exception: kb = None` **静默吞掉**。

后果：

- `self.kb.entries` 恒为空 → `_kb_context()` 恒返回 `""` → **fact Judge 的 `kb_context` 永远是空的**；
- 报告 `kb_hits` 恒为 0，但界面上不易察觉。

根因：`KBEntry`（`knowledge_base.py`）只认 Tier2 字段（`topic`/`formula`/`conditions`/…），
而 `knowledge.jsonl` 是 Tier1 概念索引（含 `name`/`grade`/`aliases`/…）。两套 schema 没对齐。

> 这条不修就进 Phase 1，等于**所有 Judge 实验都是在「没有知识库」的条件下跑的**，
> 结论不可信。建议最优先修。

### 缺陷 B（中等）`rules` 字段未进入最终报告，Web 端靠字符串匹配反推

`Orchestrator.run()` 产出了结构化的 `report["rules"]`（含 `rule_id`/`verdict`/`evidence`），
但 `run_eval.Report` 数据类没有 `rules` 字段，`to_dict()` 把它丢了。
`web.py` 只能靠扫描 `suggestions` 里是否含「规则层检出」字样来反推，
导致 **WARN 级（如联想超纲）在报告中完全丢失**。

后果：JSON / API 输出拿不到规则层证据，Phase 1 无法做「规则层 vs Judge」的一致性量化对比。

### 缺陷 C（轻微）`kb_hits` 计算表达式不可靠

`self.kb.entries and len(self.kb.retrieve(...)) or 0`：
`entries` 为空列表时短路为 `[]`（falsy）→ 结果类型不稳定。叠加缺陷 A，目前恒为 0。

---

## 2b. 第二轮复查新发现（D ~ I）

> 第一轮只查了「功能有没有跑通」，第二轮改成查「跑通的结果对不对」，
> 又发现 6 处问题，其中 **D 比缺陷 A 更严重**——A 是「没生效」，
> D 是「生效了但方向反了」。

---

### D（严重·逻辑错误）前置知识被误判为「超纲」，且方向是反的

`grade_map.analyze()` 用**集合交集**判定：

```python
if set(rec["grades"]) & allowed:   # 概念册次 ∩ 声明册次
    within.append(item)
else:
    beyond.append(item)            # 无交集 → 判超纲
```

交集为空有两种情况，被混为一谈：

1. 概念**晚于**声明年级 → 真超纲（七年级教案讲一元二次方程）✅ 应该判超纲
2. 概念**早于**声明年级 → **前置知识**（九年级教案复习有理数）❌ 不该判超纲

实测（一份九年级教案正常引用前置知识）：

| 声明年级 | 被判「超纲」 | 被判「合规」 |
|---|---|---|
| 九年级 | 有理数、一元一次方程、整式、全等三角形、一次函数 | 一元二次方程、二次函数、相似三角形 |
| 八年级 | 有理数、一元一次方程、整式、一元二次方程、二次函数、相似三角形 | 全等三角形、一次函数 |
| 七年级 | 全等三角形、一次函数、一元二次方程、二次函数、相似三角形 | 有理数、一元一次方程、整式 |

**「九年级教案提有理数」被判超纲**——这是常识性错误。
按 451 个概念全量统计，各声明年级下被判超纲的数量：

| 声明 | 被判超纲 | 占全部概念 |
|---|---:|---:|
| 七年级 | 280 | 62% |
| 八年级 | 295 | 65% |
| 九年级 | 319 | **71%** |

**年级越高，误判越多**——而真实的超纲风险恰好相反。这是方向性系统偏差。

**为什么测试没抓到**：现有 7 个年级相关测试全部只覆盖「概念晚于声明」这一个方向
（`一元二次方程` vs `七年级`），没有一个测「概念早于声明」。
`test_clean_sample_not_flagged_as_beyond` 用的是七年级样例、声明也是七年级——
七上没有更低年级的前置知识可误判，所以永远通过。

**修法**：改用序关系比较（`GRADE_ORDER` 与 `min_grade` 已经算好了，只差用上）：
概念最早册次序号 > 声明允许的最晚册次序号 → 才是真超纲。

> 不修就进 Phase 1：13 个课题横跨七~九年级，八、九年级样本会被大规模压低维度 3 分数，
> 判别力实验直接失效。

---

### E（严重）cache_key 缺 `kb_context` 与声明年级，会串味

```python
key = cache_key(text, role, temperature, model, rule_evidence)
```

进了哈希的：`text` / `role` / `temperature` / `model` / `rule_evidence`
**没进哈希的**：`kb_context`、`context`（声明年级/版本/课题/课时）

两个后果：

1. **知识库升级后缓存不失效**。目前 `kb_context` 因缺陷 A 恒为空；修好 A 之后，
   已有的 5 条缓存仍是「无知识库」状态下产生的，不清缓存就会继续用旧结果。
2. **换声明年级可能复用缓存**。实测：同一份文本分别按「七年级」「九年级」跑，
   两次共只产生 4 个缓存文件（fact/design/expr/arbitrate 各 1 个）——
   第二次完整命中第一次的缓存。本项目的核心设计之一是
   「声明学段与实际不一致 → 转为质量风险信号」，缓存串味会让这个设计失效。

（注：rule_evidence 通常随年级变化而变化，所以多数情况下会被区分开；
但只要规则层判定恰好相同，就会串味——属于偶发但后果严重的漏洞。）

---

### F（中等）聚合器对 NE 维度不做归一化，把「无法判定」当成 0 分

`aggregate()` 只累加有效维度，分母仍是固定的 100：

```python
weighted += (score / 5.0) * dim.weight   # NE 的维度直接跳过，但其权重留在分母里
```

实测：全维度 5 分 → 100.0；把维度 1（权重 20）改为 NE → **80.0**，
相当于给「无法判定」打了 0 分。

后果：40 份样本只要 NE 数量不同，总分就不可比。而 NE 恰恰容易集中在
「知识库未覆盖的课题」上——等于**知识库的缺口被算成了样本的质量问题**。

需明确二选一：按有效权重归一化（推荐），或显式声明「缺失维度按 0 计」并写进评分说明。

---

### G（中等）知识库与缓存路径依赖当前工作目录

`load_kb_assets()` 用相对路径 `os.path.join("data", "kb", ...)`，
`cache.py` 的 `CACHE_DIR` 同样是相对路径。

实测：从 `/tmp` 运行 CLI，`data/kb/` 找不到 → `grade_map=None` / `retriever=None`
→ 年级判定降级为 `R-GRADE ne（未从文本抽取到任何已知概念）`，
**报告照常输出 PASS，没有任何警告**。

这与缺陷 A 是同一类问题：**静默降级**。用户拿到一份看起来正常的报告，
实际上规则层什么都没做。

---

### H（轻微）`grade_failed` 是死变量；超纲不升级为 G0

```python
grade_fail = [f for f in findings if f.rule_id == "R-GRADE" and f.verdict == "fail"]
g0_verdict = "FAIL" if formula_fails else "NE"     # grade_fail 算了但没用
```

设计上超纲属于维度 3（权重 15%），不是 G0 一票否决——这个选择本身合理。
但「有明确的超纲结论，G0 却返回 NE（无法判定）」语义是错的，
而且留了个没用的变量。建议 G0 增加 `PASS_WITH_FINDINGS` 之类的状态，或至少改名。

---

### I（测试缺口）年级判定缺反向测试

现有用例只覆盖「概念晚于声明」：

| 用例 | 覆盖 |
|---|---|
| `test_beyond_scope` | 一元二次方程 vs 九年级/七年级 |
| `test_grade_fail_with_evidence` | 同上 |
| `test_grade_auto_retrieval_only_warns` | 同上 |
| `test_clean_sample_not_flagged_as_beyond` | 七年级样例 + 七年级声明（无更低年级可误判） |

**没有任何一条测「概念早于声明」**，所以缺陷 D 一直潜伏。
建议补：九年级声明 + 提「有理数」→ 必须判 within（前置知识，非超纲）。

---

## 3. P0-10 修复记录（已完成）

两轮复查累计 9 个问题已全部修复，78 个测试通过（原 56 + 新增 22 条回归测试）。

| 项 | 修法 | 验证 |
|---|---|---|
| **D 前置知识误判超纲** | `grade_map` 新增 `classify()`：按册次**先后序号**比较（概念最早册次 > 声明最晚册次才判超纲），`analyze()` 三分 `current / earlier / beyond`；编排器向前者注入「前置知识，不得据此判超纲」的正向提示 | 九年级全量概念判超纲数 319 → **0**（单调 280/127/0）；九年级教案复习「有理数」判 `earlier`；七年级提「一元二次方程」仍判 fail |
| **A 知识库装载静默失败** | `KBEntry` 统一视图按 `tier` 分派 `Tier1Entry/Tier2Entry.from_dict()`；`load()` 失败抛错；`load_safe()` 返回告警 | `kb_hits` 0 → **5**（fact Judge 拿到真实概念条目）；坏文件必抛错 |
| **E cache_key 串味** | 改为对**渲染后的完整提示**取哈希（`cache_key(role, temp, model, system, user, context)`），任何进入提示的输入自动进键；`PROMPT_VERSION` 升 v2 | 同文本按七/九年级跑，缓存文件 4 → **8**（不串味） |
| **F NE 当 0 分** | 按**有效权重归一化**：`total = weighted / covered_weight × 100`，附 `weight_coverage` 与 `low_coverage` 标记 | 全 5 分=100；权重 20 的维度判 NE 仍 = **100**（原为 80） |
| **B rules 未进报告** | `Report` 增加 `rules` / `warnings` 字段；Web 直接消费结构化 findings（fail/warn/ne 全留痕）而非从 suggestions 字符串反推 | JSON API 返回 3 条 findings；HTML 报告显示「规则层确定性检查（零 LLM）G0：NE」区块与 R-STRUCT warn |
| **G 路径依赖 cwd** | 新增 `edu_eval/paths.py`：以包根定位项目根，`EDU_EVAL_DATA_DIR` / `EDU_EVAL_CACHE_DIR` 环境变量权威覆盖；`load_kb_assets` 失败显式写入 `warnings` | 从 `/tmp` 跑 CLI：kb_hits=5、warnings 为空；资产缺失时报告顶部出现 ⚠ 告警，不再静默 PASS |
| **H 死变量** | `grade_failed` 改为前缀匹配（原精确匹配 `"R-GRADE"` 恒为空）；G0 语义保持「超纲属维度 3，不升级一票否决」并写明注释 | summary 不再输出恒 0 指标 |
| **C kb_hits 表达式** | 重写为 `len(entries)` 显式返回 | 类型恒为 int |
| **I 反向测试缺口** | 新增 `tests/test_p0_fixes.py`：22 条测试锁 D/A/E/F/B/G，含**反向年级**（earlier ≠ beyond）、**单调性**（超纲数随声明年级递减）、**互斥性**（三类不重叠）、**cwd 无关**（子进程从仓库外跑 CLI） | 全部通过 |

**修复过程中额外发现并修掉的问题**：

- `run_eval.evaluate()` 传入 `kb` 时把 `gm/retriever` 硬置 None（CLI 传空 KB
  导致规则层静默失效）——改为 kb 仅覆盖本体，其余资产始终装载；
- `paths.resolve()` 的环境变量覆盖原来只是「候选之一」，缺失时仍回落项目根，
  无法用于隔离测试——改为显式设置即**权威覆盖**。

---

## 4. 结论

- Phase 0 的 9 项任务 + P0-10 补漏**全部完成**：78 个测试通过，M1/M2 里程碑复核达成
  （M1：`(a+b)²=a²+b²` 零 LLM 判 FAIL；M2：Web 报告含规则层区块/雷达图/证据）。
- 复查的方法论教训已固化进测试：**只测「跑没跑通」不够，必须测「结果对不对」**，
  尤其是反方向与边界用例（本轮最严重的 D 正是藏在单向测试的盲区里）。
- 之后进入 Phase 1。若 Hy3 Key 仍未到位，可先并行做 **P1-1 Tier2 断言集骨架**
  （13 课题 × 8–15 条，sympy 校验流水线），Key 一到即可灌入。
