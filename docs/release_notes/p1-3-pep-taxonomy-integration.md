# P1-3 · pep-math-taxonomy 整合报告

> 整合日期 2026-09-03 · 任务编号 P1-3 · 关联 Phase 1 校准与 Layer 维度 1 判定

## 一、整合动机

知识库现状（整合前）：
- K12-KGraph 451 概念，**std_ref 字段全部空缺**（覆盖率 0%）
- kb_gaps 报告 9 个概念在 40 核心概念清单里缺失
- 维度 1「教学目标是否引课标条目」判定全盲，无法可信抽查教案"目标引课标"是空话还是真有

外部数据源：
- `Xww-coder/pep-math-taxonomy`（CC BY-SA 4.0 + ODbL 1.0，人教版初中数学 6 册全） 169 topic + 68 独立课标条目 + 237 前置依赖边

## 二、产出

| 项目 | 数值 | 文件位置 |
|-----|-----|---------|
| 知识库总条目 | 451 → **452** | `data/kb/knowledge.jsonl` |
| std_ref 覆盖率 | **0% → 43%**（196/452） | 同上 |
| 延伸条目数 | 0 → **1**（位似变换） | `data/kb/extensions.jsonl` |
| 课题级核心概念命中率 | **77.5% → 80.0%**（31/40 → 32/40） | `data/testsets/kb_gaps.json` |
| FTS5 索引重建 | 451 → 452 条 | `data/kb/knowledge.db` |
| 测试 | 142 → **147 passed** | `tests/` |

## 三、关键设计

### 1. 许可处理（**重要**）
- 源仓库 CC BY-SA 4.0 + ODbL 1.0，**禁止原样重发**到我们 MIT 仓库
- 处理方式：仅 schema 化引用 topic.id/standards，**不复制 description/evidence/assessmentPrompt 原文**
- 延伸条目按 SA 约束保留 CC BY-SA 4.0 协议（ext001 标 `license: "CC BY-SA 4.0"`）
- 原 JSON 始终保留在 `/tmp/pep-math-taxonomy`，不入仓
- `sources.yaml` 加 `pep-math-taxonomy` 源标注，全链路可审计

### 2. 关联规则（backfill_std_ref.py）
KB.name → topic 的三档优先级：
- R1: KB.name 是 topic.name 子串（KB 更具体, 122 条）
- R2: KB.name 出现在 topic.description 中（59 条）
- R3: 反向，KB 是大筐（14 条）

未关联 256 条（KB 拆得比 topic 细的"子概念"，如"0"、"原点"、"同号两数相加法则"），属合理空缺，**保留** std_ref=None。

### 3. 防止回流覆盖（防止消化被洗）
`ingest.py` 新增 `_restore_std_ref` 与 `_append_extensions`：
- dump_jsonl 前从已有 jsonl 恢复 std_ref（195 条 → 不丢）
- dump_jsonl 后从 `extensions.jsonl` 追加延伸条目（1 条 → 不丢）
- **支持回填脚本独立运行 + ingest 重跑整体知识库**

### 4. 延伸条目约定
ID 命名加 `_extNNN` 后缀：`math_9b_rjb_ext001`（位似）。理由：
- 避免与 K12-KGraph `math_{7-9}{a-b}_rjb_cpt{1..N}` cpt 编号体系冲突
- 一眼可辨"衍生条目"，方便审计
- 每个延伸条目走 schema.Tier1Entry 完整校验

## 四、关键修复点（顺手）

1. **测试断言硬编码 451**：延伸条目让总数变 452，断言升为 `>= 451`
2. **kb_gaps "位似"项重复**：从 missing 中移到 `resolved_via_extensions`，missing_count 9→8

## 五、复现命令

```bash
# 1. 拉外源并消化
gh repo clone Xww-coder/pep-math-taxonomy -- /tmp/pep-math-taxonomy -- --depth 1

# 2. 回填 std_ref
PYTHONPATH=src python -m edu_eval.kb.backfill_std_ref

# 3. 增量入库延伸条目（位似）
PYTHONPATH=src python -m edu_eval.kb.add_tier1_entries

# 4. 重建知识库 + merge 延伸 + FTS5 索引
PYTHONPATH=src python -m edu_eval.kb.ingest

# 5. 重建年级映射 (维度 3 数据基础)
PYTHONPATH=src python -m edu_eval.kb.grade_map

# 6. 跑回归
PYTHONPATH=src python -m pytest tests/ -q
```

## 六、当前维度 1 收益

回填完成后，`std_ref` 给出如：
- `math_7a_rjb_cpt1` 正数 → `pep-math-2022:7上.1.1`
- `math_7b_rjb_cpt54` 消元法 → `pep-math-2022:7下.10.2.1`
- `math_9a_rjb_cpt7` 配方法 → `pep-math-2022:9上.21.2`
- `math_9b_rjb_ext001` 位似变换 → `pep-math-2022:9下.27.3`

**维度 1 Judge 现在的金标是**：「目标段落是否包含至少一个 std_ref 对应课标条目的摘要词」。
（如目标包含"配方法/完全平方/ax²+bx+c=0" → 用 9上.21.2 节匹配 → 通过）

## 七、未覆盖的 256 条（按 grade 分布）

| grade | 未关联 |
|------:|------:|
| 七年级上册 | 29 |
| 七年级下册 | 45 |
| 八年级上册 | 51 |
| 八年级下册 | 52 |
| 九年级上册 | 52 |
| 九年级下册 | 27 |

特征：KB 里**拆得比 topic 细**的"内部操作"概念，如"同号两数相加法则"、"异号两数相加法则"、"移项+变号法则"等。这类概念在教学粒度上属于 topic 内的"步骤元素"，不应独立属于某个课标条目。**保留空缺属合理**。

## 八、下一步

- [ ] 维度 1 Judge Prompt 接入 std_ref 核验（算法已就绪, Prompt 待工程化）
- [ ] 校准 4 份真实样本二次评测，看 std_ref 启用后维度 1 的真评分数变化
- [ ] 当前 Hy3 额度已用完，等 9/4 13:32 重置后实测
- [ ] Phase 2 可考虑用剩余 100+ topic 提炼 Tier2 断言候选清单

## 九、改动文件清单

**新增**:
- `src/edu_eval/kb/backfill_std_ref.py`（回填主脚本）
- `src/edu_eval/kb/add_tier1_entries.py`（延伸条目入库）
- `data/kb/extensions.jsonl`（1 条位似）
- `tests/test_pep_taxonomy_integration.py`（5 项回归）

**修改**:
- `src/edu_eval/kb/ingest.py`（merge std_ref + append extensions 两步）
- `src/edu_eval/kb/sources.yaml`（增 `pep-math-taxonomy` 源）
- `data/kb/knowledge.jsonl`（195 条 std_ref + 1 位似条目）
- `data/kb/knowledge.db`（FTS5 重建）
- `data/kb/concept_grade.json`（含位似）
- `data/testsets/kb_gaps.json`（口径 77.5% → 80.0%, +resolved_via_extensions）
- `tests/test_rules_kb.py`（断言放宽至 >= 451）

---

> 一句话总结：从 pep-math-taxonomy 消化回 196 个数据点（195 std_ref + 1 新概念），知识库总条数 451 → 452，std_ref 覆盖率从 0% 提升到 43%，核心概念命中率从 77.5% 提升到 80%。所有 142+5=147 个测试通过。
