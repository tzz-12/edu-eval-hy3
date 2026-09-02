"""程序化缺陷注入器 —— 自动构造开发集的"可核验真值"来源。

对应方案 4.3：已知缺陷通过程序自动注入，包括改错公式、篡改适用条件、
加入错误推导或答案、混入超纲知识、删除关键环节和用"伪启发"话术包装灌输；
注入位置、类型和严重程度作为可核验真值随样本落盘。

设计原则：
1. 纯程序化，零 LLM —— 注入本身就是真值，不引入模型噪声；
2. 锚点诚实 —— 找不到锚点明确报 miss，绝不静默跳过；
3. 种子固定 —— 同 (seed, types) 输入产出完全一致，可复现；
4. 期望信号显式 —— 每条注入记录声明应由哪条规则 / 哪个 Judge 抓到，
   供判别力实验自动对账（注入了却没被抓 = 评测器漏检，而非样本问题）。

用法：
    PYTHONPATH=src python -m edu_eval.defects \
        --in data/samples/example_lesson.md --grade 七年级 \
        --topic 一元一次方程 --seed 42 \
        --out data/devset/doc_D135.md --manifest data/devset/manifest.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

PROMPT_VERSION = "defects-v1"

# 六类缺陷（对齐方案 4.3）
T_FORMULA = "formula_error"        # 改错公式 / 注入错误恒等式
T_CONDITION = "condition_tamper"   # 篡改适用条件
T_DERIVATION = "wrong_derivation"  # 错误推导或答案
T_OUT_GRADE = "out_of_grade"       # 混入超纲知识
T_MISSING = "missing_section"      # 删除关键环节
T_PSEUDO = "pseudo_heuristic"      # 伪启发包装灌输

ALL_TYPES = [T_FORMULA, T_CONDITION, T_DERIVATION,
             T_OUT_GRADE, T_MISSING, T_PSEUDO]

# 每类缺陷的期望信号：rules = 规则层应抓到（确定性）；
# judges = 语义 Judge 应抓到（维度 id 2 = 知识准确）。
EXPECTED_SIGNALS: Dict[str, Dict[str, List[str]]] = {
    T_FORMULA:    {"rules": ["R-FORMULA"], "judges": ["2"]},
    T_CONDITION:  {"rules": [],            "judges": ["2"]},
    T_DERIVATION: {"rules": [],            "judges": ["2"]},
    T_OUT_GRADE:  {"rules": ["R-GRADE"],   "judges": ["3"]},
    T_MISSING:    {"rules": ["R-STRUCT"],  "judges": ["4"]},
    T_PSEUDO:     {"rules": [],            "judges": ["7"]},
}


@dataclass
class DefectRecord:
    """一条注入的可核验真值。"""
    defect_id: str
    type: str
    severity: str            # high / mid / low
    anchor: str              # 命中的锚点（正则或节标题）
    before: str              # 注入前局部原文（截断存储）
    after: str               # 注入后局部原文（截断存储）
    expected: Dict[str, List[str]]
    detail: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# D1 公式错误：向锚点节注入一条经典错误恒等式（真值：R-FORMULA 应 fail）
# 素材即教学中的高频错误，与 Tier2 断言 common_errors 同源。
# --------------------------------------------------------------------------
FORMULA_ERRORS: List[Dict[str, str]] = [
    {"bad": "(x+3)² = x² + 9",
     "note": "完全平方公式漏交叉项 2·x·3"},
    {"bad": "(a+b)² = a² + b²",
     "note": "完全平方公式漏交叉项 2ab"},
    {"bad": "x/2 + x/3 = 5 的解是 x = 5/(1/2+1/3)",
     "note": "去分母漏乘常数项"},
    {"bad": "-(x - 4) = -x - 4",
     "note": "去括号不变号"},
]

# --------------------------------------------------------------------------
# D2 适用条件篡改：注入"去掉前提条件"的表述（真值：FactJudge 维度 2）
# --------------------------------------------------------------------------
CONDITION_TAMPERS: List[Dict[str, str]] = [
    {"bad": "等式两边同时除以同一个式子，等式仍然成立，不需要考虑式子是否为 0。",
     "note": "遗漏除式非零前提"},
    {"bad": "ax + b = c 型方程中，a 可以取任意值，方程总有唯一解。",
     "note": "遗漏 a≠0 前提，且一次方程并非总有解"},
    {"bad": "解方程去分母时，只需乘以含未知数的项即可。",
     "note": "去分母须乘以每一项"},
]

# --------------------------------------------------------------------------
# D3 错误推导：向例题节注入一段带符号错误的分步推导（真值：FactJudge）
# --------------------------------------------------------------------------
DERIVATION_ERRORS: List[Dict[str, str]] = [
    {"bad": "解方程 2x+3=11：移项得 2x=11+3，即 2x=14，所以 x=7。",
     "note": "移项不变号（应为 2x=11-3=8，x=4）"},
    {"bad": "解方程 x/2=3：两边同时乘以 1/2，得 x=3/2。",
     "note": "应乘以 2，得 x=6"},
    {"bad": "解方程 5x-2=3x+8：移项得 5x-3x=8-2，即 2x=6，所以 x=3。",
     "note": "常数项移项应变号（应为 8+2=10，x=5）"},
]

# --------------------------------------------------------------------------
# D4 超纲知识：按声明年级从图谱取 beyond 概念，向练习节注入显式使用句。
# 概念名原文显式出现 → 规则层 strict 判定应 fail（真值：R-GRADE）。
# --------------------------------------------------------------------------
OUT_OF_GRADE_SNIPPETS: Dict[str, str] = {
    "一元二次方程": "拓展提高：尝试用求根公式 x = (-b±√(b²-4ac))/(2a) 解一元二次方程 x²-3x+1=0。",
    "勾股定理": "拓展提高：已知直角三角形两直角边为 3 和 4，利用勾股定理求斜边长。",
    "因式分解": "拓展提高：用十字相乘法作因式分解：x²-5x+6=(x-2)(x-3)。",
    "二次函数": "拓展提高：画二次函数 y=x²-2x-3 的图象并求顶点坐标。",
    "平方根": "拓展提高：求 √2 的近似值，并说明平方根与算术平方根的区别。",
}

# 节标题锚点：注入位置（各注入器共用）
_SECTION_ANCHORS = {
    T_FORMULA:    r"例题精讲|新知讲授",
    T_CONDITION:  r"新知讲授|知识讲解|要点归纳",
    T_DERIVATION: r"例题精讲|典例",
    T_OUT_GRADE:  r"巩固练习|课堂练习|拓展",
}


def _find_section_line(text: str, pattern: str) -> Optional[int]:
    """返回匹配节标题的行号（0-based），找不到返回 None。"""
    for i, line in enumerate(text.splitlines()):
        if re.search(pattern, line):
            return i
    return None


def _clip(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


@dataclass
class InjectionResult:
    text: str
    records: List[DefectRecord] = field(default_factory=list)
    misses: List[Dict[str, str]] = field(default_factory=list)


# ---- 各注入器：(new_text, record) 或 (None, miss_reason) ----

def inject_formula_error(text: str, rng: random.Random) -> Tuple[str, Optional[DefectRecord], str]:
    line = _find_section_line(text, _SECTION_ANCHORS[T_FORMULA])
    if line is None:
        return text, None, "未找到例题/新知讲授节"
    item = rng.choice(FORMULA_ERRORS)
    lines = text.splitlines()
    payload = f"易错辨析：请判断等式「{item['bad']}」是否成立。"
    lines.insert(line + 1, payload)
    rec = DefectRecord(
        defect_id="", type=T_FORMULA, severity="high",
        anchor=_SECTION_ANCHORS[T_FORMULA],
        before=_clip(lines[line]), after=_clip(payload),
        expected=EXPECTED_SIGNALS[T_FORMULA], detail=item["note"])
    return "\n".join(lines), rec, ""


def inject_condition_tamper(text: str, rng: random.Random) -> Tuple[str, Optional[DefectRecord], str]:
    line = _find_section_line(text, _SECTION_ANCHORS[T_CONDITION])
    if line is None:
        return text, None, "未找到新知讲授节"
    item = rng.choice(CONDITION_TAMPERS)
    lines = text.splitlines()
    lines.insert(line + 1, f"要点补充：{item['bad']}")
    rec = DefectRecord(
        defect_id="", type=T_CONDITION, severity="high",
        anchor=_SECTION_ANCHORS[T_CONDITION],
        before=_clip(lines[line]), after=_clip(item["bad"]),
        expected=EXPECTED_SIGNALS[T_CONDITION], detail=item["note"])
    return "\n".join(lines), rec, ""


def inject_derivation_error(text: str, rng: random.Random) -> Tuple[str, Optional[DefectRecord], str]:
    line = _find_section_line(text, _SECTION_ANCHORS[T_DERIVATION])
    if line is None:
        return text, None, "未找到例题精讲节"
    item = rng.choice(DERIVATION_ERRORS)
    lines = text.splitlines()
    lines.insert(line + 1, item["bad"])
    rec = DefectRecord(
        defect_id="", type=T_DERIVATION, severity="high",
        anchor=_SECTION_ANCHORS[T_DERIVATION],
        before=_clip(lines[line]), after=_clip(item["bad"]),
        expected=EXPECTED_SIGNALS[T_DERIVATION], detail=item["note"])
    return "\n".join(lines), rec, ""


def pick_beyond_concept(grade: str, retriever, grade_map) -> Optional[Dict[str, str]]:
    """选一个对声明年级真超纲、且片段库里有现成注入句的概念。"""
    if retriever is None or grade_map is None:
        return None
    for name in OUT_OF_GRADE_SNIPPETS:
        hits = retriever.search(name, top_k=1)
        if not hits:
            continue
        rep = grade_map.analyze([hits[0].id], grade)
        if rep.get("parse_ok") and rep.get("beyond"):
            return {"name": hits[0].name, "id": hits[0].id,
                    "snippet": OUT_OF_GRADE_SNIPPETS[name]}
    return None


def inject_out_of_grade(text: str, grade: str, rng: random.Random,
                        retriever=None, grade_map=None
                        ) -> Tuple[str, Optional[DefectRecord], str]:
    line = _find_section_line(text, _SECTION_ANCHORS[T_OUT_GRADE])
    if line is None:
        return text, None, "未找到练习节"
    pick = pick_beyond_concept(grade, retriever, grade_map)
    if pick is None:
        return text, None, "片段库概念对声明年级均不超纲（或映射不可用）"
    lines = text.splitlines()
    lines.insert(line + 1, pick["snippet"])
    rec = DefectRecord(
        defect_id="", type=T_OUT_GRADE, severity="high",
        anchor=_SECTION_ANCHORS[T_OUT_GRADE],
        before=_clip(lines[line]), after=_clip(pick["snippet"]),
        expected=EXPECTED_SIGNALS[T_OUT_GRADE],
        detail=f"显式使用超纲概念「{pick['name']}」（{pick['id']}）")
    return "\n".join(lines), rec, ""


def inject_missing_section(text: str, rng: random.Random) -> Tuple[str, Optional[DefectRecord], str]:
    """删除一个关键节。

    真值对齐规则层契约（rules.REQUIRED_SECTIONS）：删除必备节（作业设计 /
    教学目标）→ R-STRUCT 应 warn（相对基线的增量）；删除评价设计不在必备
    清单中 → 期望信号为语义层（维度 8 教学评一致性应扣分）。
    为避免一次删掉多类信息，只删第一个命中节。
    """
    candidates = [
        ("作业布置|课后作业|作业设计|布置作业", "作业设计", True),
        ("教学目标|学习目标", "教学目标", True),
        ("评价设计|课堂评价|达成评价", "评价设计", False),
    ]
    lines = text.splitlines()
    for pattern, label, required in candidates:
        start = None
        for i, line in enumerate(lines):
            if re.search(pattern, line):
                start = i
                break
        if start is None:
            continue
        # 节的范围：到下一个同/更高级标题或文末
        end = len(lines)
        head_level = len(lines[start]) - len(lines[start].lstrip("#"))
        for j in range(start + 1, len(lines)):
            stripped = lines[j].lstrip()
            if stripped.startswith("#"):
                lvl = len(lines[j]) - len(lines[j].lstrip("#"))
                if lvl <= head_level:
                    end = j
                    break
        removed = lines[start:end]
        new_lines = lines[:start] + lines[end:]
        is_required = required
        rec = DefectRecord(
            defect_id="", type=T_MISSING, severity="mid",
            anchor=lines[start].strip(),
            before=_clip("\n".join(removed)), after="(整节删除)",
            expected=({"rules": ["R-STRUCT"], "judges": ["4"]} if is_required
                      else {"rules": [], "judges": ["8"]}),
            detail=f"删除关键节「{label}」（{len(removed)} 行）"
                   + ("，属必备节" if is_required else "，属语义层信号"))
        return "\n".join(new_lines), rec, ""
    return text, None, "未找到可删除的关键节"


PSEUDO_BODIES: List[str] = [
    "教师连续发问：“是不是这样？对不对？同意吗？”学生齐声应答后，教师直接"
    "板书结论并要求全体记录。整个过程无需学生独立思考或举例验证。",
    "设置“问题串”：这个答案对吗？很好！我们再看看下一个。随后教师逐条公布"
    "标准答案，学生核对抄写，不追问理由。",
]


def inject_pseudo_heuristic(text: str, rng: random.Random) -> Tuple[str, Optional[DefectRecord], str]:
    """把「启发探究」节整体替换为伪启发话术（保留提问句式，实为灌输）。

    真值：启发引导维度（Judge）应识别为低分 —— 这是语义层对抗样本。
    """
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.search(r"启发探究|启发引导|探究活动", line):
            start = i
            break
    if start is None:
        return text, None, "未找到启发探究节"
    head_level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("#"):
            lvl = len(lines[j]) - len(lines[j].lstrip("#"))
            if lvl <= head_level:
                end = j
                break
    original = "\n".join(lines[start:end])
    body = rng.choice(PSEUDO_BODIES)
    new_section = lines[start] + "\n" + body
    new_text = "\n".join(lines[:start]) + "\n" + new_section + \
               ("\n" + "\n".join(lines[end:]) if end < len(lines) else "")
    rec = DefectRecord(
        defect_id="", type=T_PSEUDO, severity="mid",
        anchor=lines[start].strip(),
        before=_clip(original), after=_clip(body),
        expected=EXPECTED_SIGNALS[T_PSEUDO],
        detail="伪启发：保留提问句式但直接公布答案，无学生思维活动")
    return new_text, rec, ""


_INJECTORS = {
    T_FORMULA: lambda text, ctx: inject_formula_error(text, ctx["rng"]),
    T_CONDITION: lambda text, ctx: inject_condition_tamper(text, ctx["rng"]),
    T_DERIVATION: lambda text, ctx: inject_derivation_error(text, ctx["rng"]),
    T_OUT_GRADE: lambda text, ctx: inject_out_of_grade(
        text, ctx["grade"], ctx["rng"], ctx.get("retriever"), ctx.get("grade_map")),
    T_MISSING: lambda text, ctx: inject_missing_section(text, ctx["rng"]),
    T_PSEUDO: lambda text, ctx: inject_pseudo_heuristic(text, ctx["rng"]),
}


def inject_all(text: str, grade: str, *, types: Optional[List[str]] = None,
               seed: int = 42, retriever=None, grade_map=None,
               sample_id: str = "") -> InjectionResult:
    """按固定顺序注入指定类型缺陷，产出带真值清单的对抗/缺陷样本。

    顺序固定（不随 seed 变化），保证行号类锚点在多次注入间不漂移。
    """
    rng = random.Random(seed)
    use = [t for t in ALL_TYPES if types is None or t in types]
    if T_OUT_GRADE in use and retriever is None:
        # 超纲注入需要概念映射；未显式提供时自动装载（失败则如实 miss）
        try:
            from edu_eval.eval.orchestrator import load_kb_assets
            _, gm, retr, _ = load_kb_assets()
            retriever, grade_map = retr, gm
        except Exception:
            retriever = grade_map = None
    ctx = {"rng": rng, "grade": grade,
           "retriever": retriever, "grade_map": grade_map}
    result = InjectionResult(text=text)
    for n, t in enumerate(use, 1):
        new_text, rec, miss = _INJECTORS[t](result.text, ctx)
        if rec is None:
            result.misses.append({"type": t, "reason": miss})
            continue
        rec.defect_id = f"{sample_id or 'doc'}-{n:02d}-{t}"
        result.records.append(rec)
        result.text = new_text
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="程序化缺陷注入器")
    ap.add_argument("--in", dest="src", required=True, help="原始教学设计文件")
    ap.add_argument("--grade", required=True, help="声明年级，如 七年级")
    ap.add_argument("--topic", default="", help="课题名（写入记录）")
    ap.add_argument("--types", default=",".join(ALL_TYPES),
                    help="逗号分隔的缺陷类型，默认全部")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--sample-id", default="")
    ap.add_argument("--out", required=True, help="注入后文档输出路径")
    ap.add_argument("--manifest", default="", help="真值清单 jsonl 追加路径")
    args = ap.parse_args(argv)

    from edu_eval.eval.orchestrator import load_kb_assets

    text = open(args.src, encoding="utf-8").read()
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    retriever = grade_map = None
    if T_OUT_GRADE in types:
        _, gm, retr, _ = load_kb_assets()
        grade_map, retriever = gm, retr

    res = inject_all(text, args.grade, types=types, seed=args.seed,
                     retriever=retriever, grade_map=grade_map,
                     sample_id=args.sample_id)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(res.text)
    print(f"已写入 {args.out}；注入 {len(res.records)} 条缺陷")
    for rec in res.records:
        print(f"  [{rec.type}/{rec.severity}] {rec.detail}")
    for m in res.misses:
        print(f"  [miss] {m['type']}: {m['reason']}", file=sys.stderr)
    if args.manifest:
        base = {"sample_id": args.sample_id, "source": args.src,
                "grade": args.grade, "topic": args.topic,
                "seed": args.seed, "prompt_version": PROMPT_VERSION}
        with open(args.manifest, "a", encoding="utf-8") as f:
            for rec in res.records:
                f.write(json.dumps({**base, **rec.to_dict()},
                                   ensure_ascii=False) + "\n")
        print(f"真值清单已追加至 {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
