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

import json
import math
import os
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
    # 关键区分：只有两侧自由符号集完全一致才是「恒等式/公式断言」。
    # 方程（2x+3=11）、性质描述（a+c=b+c）等条件等式不做恒等判定 → NE。
    ls, rs = le.free_symbols, re_.free_symbols
    if ls != rs:
        return EquationCheck(
            raw, "ne",
            "条件等式或方程（两侧符号集不同），非公式断言，交由 Judge",
        )
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


def load_grade_exempt(path: Optional[str] = None) -> Dict[str, dict]:
    """加载「概念→年级豁免表」。

    背景：K12-KGraph 只在概念首次作为章节主条目出现时建节点，导致部分
    贯穿性基础概念被挂到偏晚册次（如「代数式」实为七上引入，图谱挂八下）。
    直接用它判超纲会产生**系统性假阳性**——干净样本也会被判超纲。
    豁免表人工审核、逐条附教材依据，仅用于自动抽取路径。
    """
    path = path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "kb", "grade_exempt.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out: Dict[str, dict] = {}
    for e in data.get("exempt") or []:
        if e.get("name"):
            out[e["name"]] = e
    return out


REQUIRED_SECTIONS = [
    ("教学目标", ["教学目标", "学习目标"]),
    ("重难点", ["教学重点", "教学难点", "重难点"]),
    ("教学过程", ["教学过程", "教学环节", "新课讲授", "课堂练习"]),
    ("作业设计", ["作业布置", "课后作业", "作业设计", "布置作业"]),
]


class RuleEngine:
    def __init__(self, grade_map=None, retriever=None,
                 grade_exempt: Optional[Dict[str, dict]] = None):
        """grade_map: edu_eval.kb.grade_map.GradeMap（可选，供年级比对）。
        grade_exempt: 概念→年级豁免表，修正图谱收录偏差造成的假阳性。
        """
        self.grade_map = grade_map
        self.retriever = retriever
        self.grade_exempt = (load_grade_exempt() if grade_exempt is None
                             else grade_exempt)

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
    def check_grade(self, concept_ids: List[str], declared: str,
                    strict: bool = False) -> RuleFinding:
        """strict=False：越界只报 warn（自动检索的概念有误报风险，
        如「代数式」在图谱仅挂八下二次根式章节，但人教版七上已引入）。
        strict=True：越界报 fail（显式确认的核心概念，如测试场景）。"""
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
            return RuleFinding("R-GRADE", "fail" if strict else "warn",
                               ev, {"analysis": rep, "strict": strict})
        return RuleFinding("R-GRADE", "pass", declared, {"analysis": rep})

    def check_grade_by_text(self, text: str, declared: str,
                            retriever=None) -> List[RuleFinding]:
        """从文本抽取概念并分两级判定年级越界。

        分级依据不是"概念来自人工还是检索"，而是**概念在文本中是否显式出现**：
          - 显式概念（概念名原文出现在教学设计里）→ strict=True，越界判 fail。
            理由：白纸黑字写了「一元二次方程」，不存在检索误报可能。
          - 联想概念（仅由检索器召回、原文未出现）→ strict=False，越界判 warn。
            理由：图谱收录有局限（如「代数式」仅挂八下），联想结果有误报风险。

        concept 名长度 < 3 的不做显式判定（「线」「点」等短名子串误匹配率高）。
        豁免表中的概念（图谱收录偏差，如「代数式」）不参与判定，另行记录。
        """
        ret = retriever or self.retriever
        if not self.grade_map:
            return [RuleFinding("R-GRADE", "ne", "",
                                {"reason": "年级映射表未加载"})]

        explicit, associated, exempted = [], [], []
        if ret:
            seen = set()
            for hit in ret.search(text, top_k=10):
                if hit.id in seen:
                    continue
                seen.add(hit.id)
                if hit.name in self.grade_exempt:
                    exempted.append(hit.name)
                elif len(hit.name) >= 3 and hit.name in text:
                    explicit.append(hit.id)
                else:
                    associated.append(hit.id)

        out = []
        if exempted:
            out.append(RuleFinding(
                "R-GRADE-EXEMPT", "ne", "、".join(sorted(set(exempted))),
                {"reason": "图谱收录偏差已核实，不作为超纲判据",
                 "detail": [self.grade_exempt[n] for n in
                            sorted(set(exempted))]}))
        if explicit:
            f = self.check_grade(explicit, declared, strict=True)
            f.rule_id = "R-GRADE-EXPL"
            f.detail["n_concepts"] = len(explicit)
            out.append(f)
        if associated:
            f = self.check_grade(associated, declared, strict=False)
            f.rule_id = "R-GRADE-ASSOC"
            f.detail["n_concepts"] = len(associated)
            out.append(f)
        if not out:
            out.append(RuleFinding("R-GRADE", "ne", declared,
                                   {"reason": "未从文本抽取到任何已知概念"}))
        return out

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
                 concept_ids: Optional[List[str]] = None,
                 strict_grade: bool = False,
                 retriever=None) -> dict:
        findings = self.check_formulas(text)
        if concept_ids:
            # 调用方显式给了概念 ID（人工标注 / 课标锚定）→ 按调用方意图定级
            findings.append(self.check_grade(concept_ids, declared_grade,
                                             strict=strict_grade))
        elif self.grade_map:
            # 未提供概念 ID → 从文本自动抽取并分两级（显式 strict / 联想 warn）
            findings.extend(self.check_grade_by_text(
                text, declared_grade, retriever))
        else:
            findings.append(self.check_grade([], declared_grade))
        findings.extend(self.check_structure(text))

        formula_fails = [f for f in findings
                         if f.rule_id == "R-FORMULA" and f.verdict == "fail"]
        # P0-10 · H：实际规则 id 是 R-GRADE-EXPL / R-GRADE-ASSOC / R-GRADE，
        # 精确匹配 "R-GRADE" 恒为空 → 改为前缀匹配，summary 不再输出恒 0 的死指标
        grade_fail = [f for f in findings
                      if f.rule_id.startswith("R-GRADE") and f.verdict == "fail"]
        # 规则层结论：任一公式错误 → G0 直接 FAIL（红线一票否决）。
        # 注：显式超纲（R-GRADE-EXPL fail）不升级 G0 —— 超纲属维度 3 扣分项，
        # 是否构成知识错误由 fact Judge 结合该硬证据裁定。
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
    from edu_eval import paths as P

    gm = GradeMap.load(P.grade_json())
    x2 = next(cid for cid, v in gm.mapping.items() if v["name"] == "一元二次方程")
    g7 = next(cid for cid, v in gm.mapping.items() if v["name"] == "有理数")
    engine2 = RuleEngine(grade_map=gm)
    r_grade = engine2.evaluate("正文", declared_grade="七年级",
                               concept_ids=[g7, x2], strict_grade=True)
    print("\n== 年级超纲样本（七年级设计引用一元二次方程，严格模式）==")
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
