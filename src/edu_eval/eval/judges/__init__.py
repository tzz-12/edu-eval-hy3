"""五类 Judge（均基于 Hy3，不同角色提示实现专业分工）。

- fact              事实 Judge：知识准入（G0）+ 学段适配（维度 3）
- design            教学设计 Judge：维度 1 / 4 / 7 / 8 / 9
- expression_safety 表达与安全 Judge：维度 5 / 6（红线）/ A（辅助）
- review            复核 Judge：证据一致性检查
- arbitrate        仲裁 Judge：分差 >1、红线冲突或证据无效时触发
"""
