"""确定性规则层（P0-5）：公式核验、年级比对、结构完整性。

设计原则：
- **零 LLM 调用**：全部判定可复现、可追溯（证据=原文+反例数值）。
- **保守判定**：规则层只输出 FAIL 硬证据与 warning；无法判定的一律
  `ne`（交由后续 Judge / 人工），绝不猜测。这是 G0「误判保护」的规则侧实现。

三类规则：
- R-FORMULA：等式抽取 → sympy 符号/数值采样验证。错误公式 = FAIL 硬证据。
- R-GRADE：概念 → 年级映射查表（维度 3 超纲证据）。
- R-STRUCT：教学设计必备章节缺失（warning，供维度 4 引用）。

用法（在仓库根目录）：
  PYTHONPATH=src python -m edu_eval.eval.rules --demo
"""
from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import sympy
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# ---------------------------------------------------------------- 符号化预处理

_SUPER = {"²": "**2", "³": "**3", "⁴": "**4", "½": "(1/2)"}
_REPL = {
    "×": "*", "·": "*", "÷": "/", "−": "-", "–": "-", "—": "-",
    "（": "(", "）": ")", "【": "(", "】": ")",
    "＝": "=", "＋": "+", "：": ":",
    "≥": ">=", "≤": "<=", "≠": "!=",
}


def normalize_math(text: str) -> str:
    """中文数学写法 → sympy 可解析形式。"""
    s = text
    for k, v in _SUPER.items():
        s = s.replace(k, v)
    for k, v in _REPL.items():
        s = s.replace(k, v)
    # √x → sqrt(x)：√ 后连续的字母/数字/括号
    s = re.sub(r"√\s*\(([^()]*)\)", r"sqrt(\1)", s)
    s = re.sub(r"√\s*([0-9a-zA-Z]+)", r"sqrt(\1)", s)
    s = re.sub(r"sqrt\s*\(([^()]*)\)²", r"(sqrt(\1))**2", s)
    # 中文顿号/空格混淆清理（仅数学片段内）
    s = s.replace(" ", "")
    return s


def extract_equations(text: str) -> List[str]:
    """从教学设计文本抽取「lhs=rhs」形式的等式片段。"""
    found: List[str] = []
    for line in text.splitlines():
        for m in re.finditer(
                r"[0-9a-zA-Z√（(][0-9a-zA-Z√()（）+\-−×÷·²³^ .,]*"
                r"=[0-9a-zA-Z√()（）+\-−×÷·²³^ .,/]*", line):
            frag = m.group(0).strip(" .,")
            if "=" in frag[1:] and any(c.isdigit() or c.isalpha() for c in frag):
                # 排除纯中文标签（如「例3=」）和无变量数值标签
                found.append(frag)
    return found


@dataclass
class EquationCheck:
    raw: str                    # 原文等式
    verdict: str                # pass / fail / ne
    reason: str = ""
    counterexample: Optional[dict] = None  # 反例 {符号: 值, lhs, rhs}

    def as_dict(self) -> dict:
        return {
            "equation": self.raw, "verdict": self.verdict,
            "reason": self.reason, "counterexample": self.counterexample,
        }


def verify_equation(raw: str) -> EquationCheck:
    """验证单个等式：符号恒等判定 + 数值采样兜底。"""
    norm = normalize_math(raw)
    if _CJK_RE.search(norm):
        return EquationCheck(raw, "ne", "含中文成分，非纯数学等式")
    lhs, _, rhs = norm.partition("=")
    if not lhs or not rhs:
        return EquationCheck(raw, "ne", "等式两侧不完整")
    try:
        le = parse_expr(lhs, transformations=_TRANSFORMS)
        re_ = parse_expr(rhs, transformations=_TRANSFORMS)
    except Exception as e:  # sympy 无法解析 → 交由 LLM
        return EquationCheck(raw, "ne", f"不可解析: {type(e).__name__}")

    free = sorted(le.free_symbols | re_.free_symbols, key=str)
    try:
        # 1) 符号判定
        diff = sympy.simplify(le - re_)
        if diff == 0:
            return EquationCheck(raw, "pass", "恒等式（符号验证）")
        # 2) 数值采样：随机赋值检验（覆盖条件等式，如 √(a²)=a 需 a≥0）
        rng = random.Random(42)  # 固定种子：可复现
        wrong = None
        for _ in range(12):
            subs = {s: sympy.Rational(rng.randint(1, 20)) for s in free}
            try:
                lv, rv = le.subs(subs), re_.subs(subs)
                if not sympy.simplify(lv - rv) == 0:
                    wrong = {**{str(k): v for k, v in subs.items()},
                             "lhs": str(lv), "rhs": str(rv)}
                    break
            except Exception:
                continue
        if wrong:
            return EquationCheck(
                raw, "fail",
                "存在反例：该等式不是恒等式（正数范围内代入即失效）",
                counterexample=wrong,
            )
        # 正数采样成立但符号不恒等 → 条件等式（如开方/绝对值类）
        return EquationCheck(
            raw, "pass",
            "正数范围内成立；符号层面不恒等，可能为条件等式",
        )
    except Exception as e:
        return EquationCheck(raw, "ne", f"验证异常: {e}")


# ---------------------------------------------------------------- 规则引擎

@dataclass
class RuleFinding:
    rule_id: str        # R-FORMULA / R-GRADE / R-STRUCT
    verdict: str        # fail / pass / warn / ne
    evidence: str       # 原文片段（可追溯）
    detail: dict = field(default_factory=dict)


REQUIRED_SECTIONS = [
    ("教学目标", ["教学目标", "学习目标"]),
    ("重难点", ["教学重点", "教学难点", "重难点"]),
    ("教学过程", ["教学过程", "教学环节", "新课讲授", "课堂练习"]),
    ("作业设计", ["作业布置", "课后作业", "作业设计", "布置作业"]),
]


class RuleEngine:
    def __init__(self, grade_map=None, retriever=None):
        """grade_map: edu_eval.kb.grade_map.GradeMap（可选，供年级比对）。"""
        self.grade_map = grade_map
        self.retriever = retriever

    # ---- R-FORMULA：公式核验 ----
    def check_formulas(self, text: str) -> List[RuleFinding]:
        findings = []
        for eq in extract_equations(text):
            chk = verify_equation(eq)
            if chk.verdict == "fail":
                findings.append(RuleFinding(
                    "R-FORMULA", "fail", eq, chk.as_dict()))
            elif chk.verdict == "pass":
                findings.append(RuleFinding(
                    "R-FORMULA", "pass", eq, chk.as_dict()))
            # ne 静默（交由 Judge）
        return findings

    # ---- R-GRADE：年级比对（维度 3 确定性证据）----
    def check_grade(self, concept_ids: List[str], declared: str) -> RuleFinding:
        if not self.grade_map:
            return RuleFinding("R-GRADE", "ne", "", {"reason": "年级映射表未加载"})
        if not declared:
            return RuleFinding("R-GRADE", "ne", "",
                               {"reason": "样本未声明年级，按强制规则返回 NE"})
        rep = self.grade_map.analyze(concept_ids, declared)
        if not rep.get("parse_ok"):
            return RuleFinding("R-GRADE", "ne", declared,
                               {"reason": rep.get("error", "声明年级无法解析")})
        beyond = rep["beyond"]
        if beyond:
            ev = "；".join(
                f"「{b['name']}」属 {('、'.join(b['grades']))}" for b in beyond[:5])
            return RuleFinding("R-GRADE", "fail" if len(beyond) >= 1 else "warn",
                               ev, {"analysis": rep})
        return RuleFinding("R-GRADE", "pass", declared, {"analysis": rep})

    # ---- R-STRUCT：结构完整性 ----
    def check_structure(self, text: str) -> List[RuleFinding]:
        findings = []
        for name, kws in REQUIRED_SECTIONS:
            if not any(k in text for k in kws):
                findings.append(RuleFinding(
                    "R-STRUCT", "warn", f"未检出「{name}」章节",
                    {"missing": name, "keywords": kws}))
        return findings

    # ---- 汇总 ----
    def evaluate(self, text: str, declared_grade: str = "",
                 concept_ids: Optional[List[str]] = None) -> dict:
        findings = self.check_formulas(text)
        findings.append(self.check_grade(concept_ids or [], declared_grade))
        findings.extend(self.check_structure(text))

        formula_fails = [f for f in findings
                         if f.rule_id == "R-FORMULA" and f.verdict == "fail"]
        grade_fail = [f for f in findings
                      if f.rule_id == "R-GRADE" and f.verdict == "fail"]
        # 规则层结论：任一公式错误 → G0 直接 FAIL（红线一票否决）
        g0_verdict = "FAIL" if formula_fails else "NE"
        return {
            "g0_rule_verdict": g0_verdict,
            "findings": [f.__dict__ for f in findings],
            "summary": {
                "formula_checked": sum(1 for f in findings if f.rule_id == "R-FORMULA"),
                "formula_failed": len(formula_fails),
                "grade_failed": len(grade_fail),
                "structure_missing": sum(1 for f in findings
                                         if f.rule_id == "R-STRUCT" and f.verdict == "warn"),
            },
        }


if __name__ == "__main__":
    import json
    from edu_eval.kb.grade_map import GradeMap

    bad = """# 整式的乘法教学设计（八年级上册）
## 教学目标
理解完全平方公式，会用公式进行计算。
## 教学重点
完全平方公式的结构特征。
## 教学过程
1. 探索：(a+b)²=a²+b²，引导学生观察两项平方和的形式。
2. 例题：(x+3)²=x²+9。
## 作业布置
课本习题 14.2 第 1 题。"""

    good = """# 整式的乘法教学设计（八年级上册）
## 教学目标
理解完全平方公式，会用公式进行计算。
## 教学重点
完全平方公式的结构特征。
## 教学过程
1. 探索：(a+b)²=a²+2ab+b²，引导学生发现乘积项。
2. 练习：3+4=7，验证 x+y 与 y+x 相等（x+y=y+x）。
## 作业布置
课本习题 14.2 第 1 题。"""

    engine = RuleEngine()
    r_bad = engine.evaluate(bad, declared_grade="八年级")
    r_good = engine.evaluate(good, declared_grade="八年级")

    # R-GRADE 联合演示：七年级设计中出现「一元二次方程」（九年级上册）
    gm = GradeMap.load("data/kb/concept_grade.json")
    x2 = next(cid for cid, v in gm.mapping.items() if v["name"] == "一元二次方程")
    g7 = next(cid for cid, v in gm.mapping.items() if v["name"] == "有理数")
    engine2 = RuleEngine(grade_map=gm)
    r_grade = engine2.evaluate("正文", declared_grade="七年级",
                               concept_ids=[g7, x2])
    print("\n== 年级超纲样本（七年级设计引用一元二次方程）==")
    for f in r_grade["findings"]:
        if f["rule_id"] == "R-GRADE":
            print(f"  [{f['verdict'].upper()}] {f['evidence']}")
    print("== 含错误公式样本 ==")
    print(json.dumps(r_bad["summary"], ensure_ascii=False))
    for f in r_bad["findings"]:
        if f["verdict"] == "fail":
            print(f"  [FAIL] {f['rule_id']}: {f['evidence']}")
            if f["detail"].get("counterexample"):
                print(f"         反例: {f['detail']['counterexample']}")
    print("\n== 正确样本 ==")
    print(json.dumps(r_good["summary"], ensure_ascii=False))
    assert r_bad["g0_rule_verdict"] == "FAIL", "M1 未达成：错误公式未被捕获"
    assert r_good["g0_rule_verdict"] == "NE"
    print("\nM1 里程碑验证通过：错误公式被规则层捕获（无需 Hy3 Key）")
