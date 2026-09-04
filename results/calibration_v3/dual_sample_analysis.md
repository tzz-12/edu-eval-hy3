# 双采样真实跑一致性分析（修正口径）

- 样本数: 4  | 评估维度对(两次视角): 40
- 配置: model=None  threshold=1  mock=None

## 1. 自一致总览
- 走 average 路径: 31 / 40
- 触发层内仲裁: 9（其中真不确定性 arbitrated_ne: 4）
- 两次视角分差=0 (完全一致): 28
- 分差>1 (明显分歧): 6
- 分差均值: 0.459  | 分差最大值: 4
- 完全失败维度(连仲裁无分): 3

## 2. 各 Judge 稳定性 (按两次视角分差，仅数值维度对)
| Judge | 维度对数 | 分差均值 | 分差最大 | 完全一致数 |
|---|---|---|---|---|
| FactJudge(维度2/3) | 8 | 0.75 | 4 | 6 |
| DesignJudge(1/4/7/8/9) | 20 | 0.35 | 2 | 15 |
| ExprSafety(5/6/A) | 9 | 0.444 | 2 | 7 |

## 3. 触发层内仲裁的具体维度
| 样本 | Judge | 维度 | a | b | 重算diff | resolution |
|---|---|---|---|---|---|---|
| 二次函数的图象和性质.md | design | 8 | 5 | 3 | 2 | arbitrated |
| 二次函数的图象和性质.md | design | 9 | 5 | 3 | 2 | arbitrated |
| 二次函数的图象和性质.md | expression_safety | A | 1 | 3 | 2 | arbitrated |
| 平行四边形性质.md | expression_safety | 6 | 3 | 5 | 2 | arbitrated |
| 丰富多彩的正方形.md | fact | 2 | 3 | 5 | 2 | arbitrated_ne |
| 平面镶嵌.md | fact | 2 | 1 | 5 | 4 | arbitrated |
| 平面镶嵌.md | expression_safety | 5 | None | None | None | arbitrated_ne |
| 平面镶嵌.md | expression_safety | 6 | None | None | None | arbitrated_ne |
| 平面镶嵌.md | expression_safety | A | None | None | None | arbitrated_ne |

## 4. 完全失败维度（a/b/score 全 None，需排查 Judge 输出）
| 样本 | Judge | 维度 | resolution |
|---|---|---|---|
| 平面镶嵌.md | expression_safety | 5 | arbitrated_ne |
| 平面镶嵌.md | expression_safety | 6 | arbitrated_ne |
| 平面镶嵌.md | expression_safety | A | arbitrated_ne |

## 5. ² 修复验证：好样本是否仍被误判 FAIL (维度2 + 准入)
| 样本 | 维度2分 | admission | redline |
|---|---|---|---|
| 二次函数的图象和性质.md | 5 | PASS | False |
| 平行四边形性质.md | 5 | PASS | False |
| 丰富多彩的正方形.md | 5 | NE | False |
| 平面镶嵌.md | 1 | NE | False |

## 6. 样本级 NE（双采样保守降级）: ['丰富多彩的正方形.md', '平面镶嵌.md']
- 成因：FactJudge 两次视角(strict_rubric/learner_view)的 G0 准入判定不一致 → 保守定为 NE，
  而非给出可能错误的分数。这恰是非确定性被捕获的证据（见 §3/§4 的不稳定维度）。

## 7. 成本
- API 调用: 42  | 缓存命中: 0  | 层内仲裁: 9