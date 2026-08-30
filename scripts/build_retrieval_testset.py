"""检索测试集生成（P0-9）：查询 + 期望命中概念 ID。

设计原则：**期望答案独立于检索器**（避免循环论证）。
    ground truth 直接从 knowledge.jsonl（K12-KGraph 导入结果）按概念名/
    别名精确查表得到，检索器只负责"被考"，不参与答案生成。

四类查询：
1. name       —— 概念名原样查询（13 课题核心概念 + 随机补充）
2. alias      —— 别名/俗称查询（考察同义召回，期望命中该概念）
3. context    —— 教学设计自然句（考察句中召回，期望命中指定概念）
4. distractor —— 高中概念/超纲术语（期望不命中初中集合，考察误报）

用法（仓库根目录，先 ingest）：
  PYTHONPATH=src python scripts/build_retrieval_testset.py
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from edu_eval.kb.retriever import KBRetriever  # noqa: E402

# 13 课题核心概念名（kb_scope.md 闭环范围）
TOPIC_CONCEPTS = [
    "有理数", "有理数的加法", "绝对值", "数轴", "相反数",
    "整式", "同类项", "合并同类项",
    "一元一次方程", "等式的性质", "移项",
    "二元一次方程组", "代入消元法", "加减消元法",
    "全等三角形", "全等三角形的判定",
    "因式分解", "提公因式法", "公式法",
    "分式", "分式的基本性质", "通分",
    "勾股定理", "勾股定理的逆定理",
    "一次函数", "正比例函数", "待定系数法",
    "一元二次方程", "配方法", "求根公式",
    "二次函数", "抛物线", "顶点",
    "相似三角形", "相似比", "位似",
    "锐角三角函数", "正弦", "余弦", "正切",
]

# 语境查询（教学设计中的自然表述）：(句子, 期望概念名 或 None 表示期望可空)
CONTEXT_QUERIES = [
    ("本节课我们学习勾股定理及其逆定理的应用", "勾股定理"),
    ("教学重点：掌握合并同类项的方法", "合并同类项"),
    ("通过待定系数法确定一次函数的表达式", "待定系数法"),
    ("用配方法解一元二次方程 x²-4x+3=0", "配方法"),
    ("复习提问：什么叫同类项？", "同类项"),
    ("例 3：利用公式法分解因式", "公式法"),
    ("二次函数 y=ax²+bx+c 的图象是抛物线", "二次函数"),
    ("分式的分母不能为零，这是分式有意义的条件", "分式"),
    ("锐角三角函数中，sinA 表示角 A 的正弦", "正弦"),
    ("两个三角形相似的判定定理", "相似三角形"),
    ("全等三角形对应边相等、对应角相等", "全等三角形"),
    ("数轴上右边的数总比左边的数大", "数轴"),
    ("引导学生回顾有理数的加减法法则", "有理数"),
    ("解方程的关键是灵活运用等式的性质", "等式的性质"),
    ("用加减消元法解二元一次方程组", "加减消元法"),
    ("把方程中的某一项改变符号后移到另一边，叫做移项", "移项"),
    ("先把二次三项式配方，再用平方差公式分解", None),
    ("先通分，再进行分式的加减运算", "通分"),
    ("提取公因式是因式分解的第一步", "提公因式法"),
    ("正比例函数是一次函数的特殊情形", "正比例函数"),
    ("利用判别式判断一元二次方程根的情况", None),
    ("抛物线的顶点坐标决定了函数的最大值", "顶点"),
    ("位似图形一定是相似图形", "位似"),
    ("相似比等于对应边的比", "相似比"),
    ("在直角三角形中，正切等于对边比邻边", "正切"),
    ("余弦值随锐角的增大而减小", "余弦"),
    ("绝对值的几何意义是数轴上点到原点的距离", "绝对值"),
    ("互为相反数的两个数之和为零", "相反数"),
    ("单项式和多项式统称为整式", "整式"),
    ("分母中含有字母的式子叫做分式", "分式"),
    ("求代数式的值之前要先化简", None),          # 图谱收录局限，宽松
    ("天平两边同时加减相同的数等式仍成立", None),
]

# 干扰查询：高中概念/超纲术语 → 期望不命中初中集合
# 说明：已从列表中剔除两类"伪干扰项"——
#   ①「矩阵」：图谱七下确有该节点（人教版七下涉及矩阵表示），非误报；
#   ②「双曲线」：正确关联到「反比例函数的图象」，属于合理的跨学段关联。
DISTRACTOR_QUERIES = [
    "导数", "微分", "积分", "复数", "虚数", "行列式",
    "排列组合", "二项式定理", "椭圆的离心率", "参数方程",
    "数列的通项公式", "数学归纳法", "平面向量", "空间向量",
    "充分必要条件", "反函数", "对数函数", "指数方程",
    "线性回归", "正态分布", "条件概率", "极限", "连续性",
    "洛必达法则", "泰勒展开", "柯西不等式",
]


def _index_by_name(jsonl_path: str) -> dict:
    """从数据源建立「概念名 → [id]」索引（ground truth 来源，不依赖检索器）。"""
    idx: dict = {}
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("quarantined"):
                continue
            idx.setdefault(obj["name"], []).append(obj["id"])
    return idx


def _index_by_alias(jsonl_path: str) -> dict:
    """「别名 → [id]」索引；别名已在 ingest 阶段过滤单字脏数据。"""
    idx: dict = {}
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("quarantined"):
                continue
            for al in obj.get("aliases") or []:
                if len(al) >= 2:
                    idx.setdefault(al, []).append(obj["id"])
    return idx


def build(jsonl_path: str, out_path: str, max_name_queries: int = 60,
          max_alias_queries: int = 20) -> list:
    rng = random.Random(7)
    name_idx = _index_by_name(jsonl_path)
    alias_idx = _index_by_alias(jsonl_path)

    # 检索器仅用于生成时的自检统计，不参与期望答案
    ret = KBRetriever(db_path=os.path.join(
        os.path.dirname(jsonl_path), "knowledge.db"), jsonl_path=jsonl_path)

    queries = []

    # 1) 概念名查询
    names = list(TOPIC_CONCEPTS)
    all_names = list(name_idx.keys())
    extra = [n for n in all_names if n not in names and 2 <= len(n) <= 6]
    rng.shuffle(extra)
    names += extra[:max(0, max_name_queries - len(names))]
    for nm in names:
        hit = ret.search(nm, top_k=1)
        actual = hit[0].id if hit else None
        queries.append({
            "query": nm, "kind": "name",
            "expected_ids": name_idx.get(nm, []),          # 数据源独立给出
            "selfcheck_actual": actual,
        })

    # 2) 别名查询
    alias_pool = [a for a in alias_idx if a not in name_idx and len(a) <= 8]
    rng.shuffle(alias_pool)
    for al in alias_pool[:max_alias_queries]:
        hit = ret.search(al, top_k=1)
        queries.append({
            "query": al, "kind": "alias",
            "expected_ids": alias_idx[al],
            "selfcheck_actual": hit[0].id if hit else None,
        })

    # 3) 语境查询
    for q, expect in CONTEXT_QUERIES:
        hit = ret.search(q, top_k=1)
        expected = name_idx.get(expect, []) if expect else []
        queries.append({
            "query": q, "kind": "context",
            "expected_ids": expected,
            "expect_name": expect,
            "selfcheck_actual": hit[0].id if hit else None,
        })

    # 4) 干扰查询
    for q in DISTRACTOR_QUERIES:
        hit = ret.search(q, top_k=1)
        queries.append({
            "query": q, "kind": "distractor",
            "expected_ids": [],
            "note": "期望不命中初中概念集合",
            "selfcheck_actual": hit[0].id if hit else None,
        })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for q in queries:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    return queries


def build_gaps(jsonl_path: str, out_path: str, ret: KBRetriever) -> dict:
    """13 课题核心概念的**知识库覆盖缺口**清单。

    图谱对"方法类/性质类"概念（如「配方法」「通分」「待定系数法」）收录不全，
    而这类概念恰恰是教学设计文本中出现频率最高的词。缺口清单是 Phase 1
    用 Hy3 补充 Tier1 条目的直接输入。
    """
    name_idx = _index_by_name(jsonl_path)
    gaps = []
    for c in TOPIC_CONCEPTS:
        if c in name_idx:
            continue
        hs = ret.search(c, top_k=2)
        gaps.append({
            "missing_concept": c,
            "nearest": [{"id": h.id, "name": h.name, "grade": h.grade,
                         "match_type": h.match_type} for h in hs],
        })
    total = len(TOPIC_CONCEPTS)
    report = {
        "total_topic_concepts": total,
        "missing_count": len(gaps),
        "coverage_pct": round((total - len(gaps)) / total * 100, 1),
        "gaps": gaps,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return report


def selfcheck(queries: list, ret: KBRetriever) -> dict:
    """生成时自检：期望 vs 检索器实际召回（同时统计 top1 / top3）。

    说明：编排器实际取 top_k=5 作为上下文，不依赖 top1；因此 top3 命中率
    是比 top1 更贴近真实用途的指标，top1 仅作排序质量的观测。
    """
    stats = {}
    for q in queries:
        exp = set(q["expected_ids"])
        hits = [h.id for h in ret.search(q["query"], top_k=3)]
        if q["kind"] == "distractor":
            ok1 = not hits
            ok3 = not hits
        else:
            ok1 = bool(hits) and hits[0] in exp
            ok3 = bool(exp & set(hits))
        k = q["kind"]
        s = stats.setdefault(
            k, {"total": 0, "top1_ok": 0, "top3_ok": 0, "no_expected": 0})
        s["total"] += 1
        s["top1_ok"] += int(ok1)
        s["top3_ok"] += int(ok3)
        if not exp:
            s["no_expected"] += 1
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default="data/kb/knowledge.jsonl")
    ap.add_argument("--out", default="data/testsets/retrieval_queries.jsonl")
    ap.add_argument("--gaps-out", default="data/testsets/kb_gaps.json")
    args = ap.parse_args()

    queries = build(args.jsonl, args.out)
    ret = KBRetriever(db_path=os.path.join(
        os.path.dirname(args.jsonl), "knowledge.db"), jsonl_path=args.jsonl)
    stats = selfcheck(queries, ret)

    gap = build_gaps(args.jsonl, args.gaps_out, ret)
    print(f"\n知识库覆盖：13 课题核心概念 {gap['total_topic_concepts']} 个，"
          f"图谱缺失 {gap['missing_count']} 个，覆盖率 {gap['coverage_pct']}%"
          f" → {args.gaps_out}")
    for g in gap["gaps"]:
        near = "、".join(f"{n['name']}（{n['grade']}）"
                        for n in g["nearest"][:2]) or "无近似"
        print(f"  缺「{g['missing_concept']}」 近似：{near}")
    print()

    print(f"检索测试集：{len(queries)} 条 → {args.out}")
    t1 = t3 = 0
    for k, s in stats.items():
        denom = s["total"] - s["no_expected"]
        if denom:
            r1 = f"{s['top1_ok']/denom*100:3.0f}%"
            r3 = f"{s['top3_ok']/denom*100:3.0f}%"
        else:
            # distractor：分母为总数（期望"零召回"）
            r1 = f"{(s['total']-s['top1_ok'])/s['total']*100:3.0f}%" if False else "  -"
            r3 = f"{s['top1_ok']/s['total']*100:3.0f}%"
        print(f"  {k:11s} {s['total']:3d} 条 | top1 {s['top1_ok']:3d} ({r1}) "
              f"| top3 {s['top3_ok']:3d} ({r3}) | 无期望 {s['no_expected']}")
        t1 += s["top1_ok"]
        t3 += s["top3_ok"]
    print(f"  合计：top1 {t1}/{len(queries)}，top3 {t3}/{len(queries)}")


if __name__ == "__main__":
    main()
