"""缺陷注入器（P0-9）：从干净样本生成带已知缺陷的评测样本。

每条注入记录：位置 / 类型 / 严重程度 / 期望准入状态 / 期望受影响维度，
供 Phase 1 的判别力实验（§4.4）做精确对照。

缺陷类型（10 类，对应 DESIGN §4.4）：
  formula_wrong   公式知识错误        → 期望 admission=FAIL（规则层即可判）
  term_wrong      术语/概念错误       → 期望 FAIL 或维度2低分
  grade_beyond    学段超纲           → 期望 PASS+维度3低分（严格模式 FAIL）
  no_goals        目标缺失/模糊       → 维度1
  no_structure    章节结构缺失        → 维度4
  no_heuristic    启发引导缺失        → 维度9
  no_learner      学情分析缺失        → 维度7
  no_assess       评价设计缺失        → 维度8
  vague_period    课时信息矛盾       → 元数据缺陷
  pseudo_quality  伪启发包装（对抗）  → 全维度（高难度对抗样本）

用法（仓库根目录）：
  PYTHONPATH=src python scripts/inject_defects.py \
      --base data/samples/example_lesson.md --out data/samples/defects.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# 每类缺陷的注入操作（对 base 文本做字符串替换/删除）
# applies_to：适用的课题，用于区分「课题不匹配」与「锚点缺失」两种跳过
DEFECT_RECIPES = [
    {
        "type": "formula_wrong",
        "severity": "critical",
        "expected_admission": "FAIL",
        "expected_dims": ["2"],
        "applies_to": "一元一次方程",
        "desc": "去括号法则错误（解一元一次方程的典型错误）：a-(b-c)=a-b-c",
        "old": "板书 a+c=b+c 与 ac=bc(c≠0)。",
        "new": ("板书 a+c=b+c 与 ac=bc(c≠0)。\n"
                "- 去括号法则：a-(b-c)=a-b-c，a+(b-c)=a+b-c。"),
    },
    {
        "type": "formula_wrong",
        "severity": "critical",
        "expected_admission": "FAIL",
        "expected_dims": ["2"],
        "applies_to": "整式/乘法公式",
        "desc": "完全平方公式漏 2ab 项（八年级整式课题）",
        "old": "(a+b)²=a²+2ab+b²",
        "new": "(a+b)²=a²+b²",
    },
    {
        "type": "formula_wrong",
        "severity": "critical",
        "expected_admission": "FAIL",
        "expected_dims": ["2"],
        "applies_to": "一元一次方程",
        "desc": "等式性质错误：两边乘 c 未限定 c≠0",
        "old": "ac=bc(c≠0)",
        "new": "ac=bc",
    },
    {
        "type": "term_wrong",
        "severity": "critical",
        "expected_admission": "FAIL",
        "expected_dims": ["2"],
        "desc": "术语错误：把等式性质说成不等式性质",
        "old": "等式的两个基本性质",
        "new": "不等式的两个基本性质",
    },
    {
        "type": "grade_beyond",
        "severity": "major",
        "expected_admission": "PASS",
        "expected_dims": ["3"],
        "desc": "七年级设计插入九年级概念（一元二次方程求根公式）",
        "old": "## 五、启发探究",
        "new": ("## 五、拓展\n补充：形如 ax²+bx+c=0 的一元二次方程可用求根公式\n"
                "x=(-b±√(b²-4ac))/(2a) 直接求解。\n\n## 六、启发探究"),
    },
    {
        "type": "no_goals",
        "severity": "major",
        "expected_admission": "PASS",
        "expected_dims": ["1"],
        "desc": "目标替换为不可观察的空话",
        "old": ("1. 能结合具体情境列出一元一次方程，并说明未知数代表的实际意义（可观察、可评价）。\n"
                "2. 能说出等式的两个基本性质，并用其解释移项依据（对应课标“方程与不等式”要求）。\n"
                "3. 会解形如 ax+b=c 的简单一元一次方程，正确率达标。"),
        "new": "1. 培养学生的数学核心素养和逻辑思维能力。\n2. 激发学习兴趣，养成良好习惯。",
    },
    {
        "type": "no_structure",
        "severity": "moderate",
        "expected_admission": "PASS",
        "expected_dims": ["4"],
        "applies_to": "一元一次方程",
        "desc": "删除教学环节章节（改为无实质环节的标题）",
        "old": ("## 三、教学环节\n"
                "- 情境导入：用“买文具找零”问题引出未知量。\n"
                "- 新知讲授：通过天平演示等式性质 1、2，板书 a+c=b+c 与 ac=bc(c≠0)。\n"
                "- 例题精讲：解方程 2x+3=11，分步写出依据。\n"
                "- 巩固练习：完成 3 道同类题，同桌互查。\n"
                "- 小结作业：小结移项法则；作业为课本练习第 1、2 题。"),
        "new": ("## 三、课堂说明\n"
                "按照教材和教参的安排进行，教师自行把握。"),
    },
    {
        "type": "no_heuristic",
        "severity": "major",
        "expected_admission": "PASS",
        "expected_dims": ["9"],
        "desc": "删除启发探究环节（直接给结论）",
        "old": ("设置阶梯问题：“如果方程两边同时平方，等式还成立吗？为什么？”引导学生发现\n"
                "“等式性质只保证恒等变形”，鼓励举例验证而非直接给结论。"),
        "new": "教师直接讲授等式性质的注意事项，学生记忆并默写。",
    },
    {
        "type": "no_learner",
        "severity": "moderate",
        "expected_admission": "PASS",
        "expected_dims": ["7"],
        "desc": "删除学情分析",
        "old": ("学生已掌握有理数四则运算与代数式表示，但对“用等式表示相等关系”缺乏系统认识，\n"
                "常见误区是把“解方程”等同于“移项变号”的机械操作，不理解等式性质。"),
        "new": "学生基本情况一般。",
    },
    {
        "type": "no_assess",
        "severity": "major",
        "expected_admission": "PASS",
        "expected_dims": ["8"],
        "desc": "删除评价设计（无达成证据）",
        "old": ("## 四、评价设计\n"
                "- 课堂：口头提问“为什么 c 不能为 0？”检查理解。\n"
                "- 练习：3 道题正确率作为达成证据。\n"
                "- 作业：批改后记录错误类型，下节课补救。"),
        "new": "## 四、评价设计\n按考试分数评价。",
    },
    {
        "type": "pseudo_quality",
        "severity": "adversarial",
        "expected_admission": "PASS",
        "expected_dims": ["1", "4", "9"],
        "desc": "伪启发包装：满篇探究话术但无实质问题链（对抗样本）",
        "old": "- 巩固练习：完成 3 道同类题，同桌互查。",
        "new": ("- 探究活动：请同学们自主探究、小组合作、深入思考、发散思维，\n"
                "  在探究中感悟，在合作中成长，充分体现学生的主体地位。\n"
                "- 巩固练习：完成 3 道同类题，同桌互查。"),
    },
]


def inject(base_text: str, topic: str = "") -> list:
    samples = []
    for i, r in enumerate(DEFECT_RECIPES):
        rec = {
            "sample_id": f"defect-{i+1:02d}",
            "type": r["type"], "severity": r["severity"], "desc": r["desc"],
            "applies_to": r.get("applies_to", ""),
            "expected_admission": r["expected_admission"],
            "expected_dims": r["expected_dims"],
        }
        # 课题不匹配 → 跳过（不是缺陷，是配方适用范围不同）
        if topic and r.get("applies_to") and r["applies_to"] not in topic \
                and topic not in r["applies_to"]:
            rec.update({"injected": False, "text": None,
                        "skip_reason": "topic_mismatch"})
            samples.append(rec)
            continue
        if r["old"] not in base_text:
            # 锚点缺失 → 记录但不伪造（保证注入位置真实可追溯）
            rec.update({"injected": False, "text": None,
                        "skip_reason": "anchor_missing",
                        "anchor": r["old"][:60]})
            samples.append(rec)
            continue
        rec.update({
            "position": {"old": r["old"][:60], "new": r["new"][:60]},
            "injected": True,
            "text": base_text.replace(r["old"], r["new"]),
        })
        samples.append(rec)
    return samples


def _fingset(rep: dict) -> set:
    """把规则层 findings 归一为可比较的集合。"""
    return {(f["rule_id"], f["verdict"], f["evidence"][:80])
            for f in rep["findings"]}


def verify_with_rules(samples: list, base_text: str,
                      declared_grade: str) -> dict:
    """规则层检出验证：**与原样本基线对比**，只统计新增判定。

    为什么要对比基线：原始样本自身就有规则层告警（如缺少板书/作业章节），
    若直接统计绝对判定，所有注入样本都会显示 WARN，指标失去判别力。
    只有"注入前没有、注入后出现"的判定才算真正的检出。
    """
    from edu_eval.eval.rules import RuleEngine
    from edu_eval.kb.grade_map import GradeMap
    from edu_eval.kb.retriever import KBRetriever

    try:
        gm = GradeMap.load("data/kb/concept_grade.json")
    except Exception:
        gm = None
    engine = RuleEngine(grade_map=gm)
    try:
        ret = KBRetriever()
    except Exception:
        ret = None

    def run(text: str) -> dict:
        # 不传 concept_ids：让引擎自动抽概念并分两级判定
        # （显式出现→strict fail；检索联想→warn）
        return engine.evaluate(text, declared_grade=declared_grade,
                              retriever=ret)

    baseline = _fingset(run(base_text))
    stats = {"checked": 0, "detected_fail": 0, "detected_warn": 0,
             "not_detected": 0, "baseline_warns": len(
                 {f for f in baseline if f[1] == "warn"})}
    for s in samples:
        if not s.get("injected"):
            continue
        stats["checked"] += 1
        rep = run(s["text"])
        new = _fingset(rep) - baseline
        fails = [f for f in new if f[1] == "fail"]
        warns = [f for f in new if f[1] == "warn"]
        s["rule_verdict"] = ("FAIL(新增)" if fails else
                             ("WARN(新增)" if warns else "未检出"))
        s["rule_new_findings"] = [
            {"rule_id": f[0], "verdict": f[1], "evidence": f[2]} for f in new]
        if fails:
            stats["detected_fail"] += 1
        elif warns:
            stats["detected_warn"] += 1
        else:
            stats["not_detected"] += 1
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="data/samples/example_lesson.md")
    ap.add_argument("--out", default="data/samples/defects.jsonl")
    ap.add_argument("--topic", default="一元一次方程",
                    help="基础样本所属课题，用于匹配 applies_to")
    ap.add_argument("--grade", default="七年级",
                    help="声明年级，供规则层做超纲判定")
    args = ap.parse_args()

    with open(args.base, encoding="utf-8") as f:
        base_text = f.read()

    samples = inject(base_text, topic=args.topic)

    # 规则层检出验证（需要知识库；缺失时降级跳过）
    try:
        stats = verify_with_rules(samples, base_text, args.grade)
    except Exception as e:
        stats = None
        print(f"（规则层验证跳过：{type(e).__name__}: {e}）")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    ok = sum(1 for s in samples if s["injected"])
    print(f"缺陷样本：{ok}/{len(samples)} 注入成功 → {args.out}")
    for s in samples:
        if s["injected"]:
            rv = s.get("rule_verdict", "-")
            mark = f"✓ 规则层={rv}"
        elif s.get("skip_reason") == "topic_mismatch":
            mark = "- 课题不匹配（配方适用于其他课题）"
        else:
            mark = "✗ 锚点缺失"
        print(f"  [{s['severity']:11s}] {s['type']:14s} {mark:32s} {s['desc'][:26]}")

    if stats:
        print(f"\n规则层检出（零 LLM 调用；基线样本自身有 "
              f"{stats['baseline_warns']} 条告警，以下仅统计新增判定）：")
        print(f"  共 {stats['checked']} 条注入样本 → "
              f"判 FAIL {stats['detected_fail']} / "
              f"判 WARN {stats['detected_warn']} / "
              f"未检出 {stats['not_detected']}")
        print("  注：规则层只覆盖确定性缺陷（公式恒等、年级越界、结构完整性）；"
              "\n      目标空泛/伪启发包装/学情缺失等属语义层缺陷，"
              "需 LLM Judge，Phase 1 接入 Hy3 后做判别力实验。")


if __name__ == "__main__":
    main()
