"""Tier 2 可核验断言集（P1-1）：Hy3 生成 + 确定性校验 + 一致性复核。

设计依据
--------
`docs/kb_scope.md` §2/§3：Tier 2 只覆盖 13 个核心课题，13 × 8–15 条 ≈ 126–165 条
可核验断言，用于 G0 知识准入判定。范围外课题按设计返回 NE（可声明的边界）。

关键约束（本项目 Hy3-only，但**不盲信模型**）
---------------------------------------------
断言由 Hy3 生成，但**生成 ≠ 可信**。每条断言必须通过确定性校验才能进入 G0：

- 能算的（公式、恒等式、方程求根）→ **sympy 真算**：算对 verified，
  找出反例 quarantined，算不了 unverified；
- 算不了的（结构性/性质类断言，如 SSS 判定、等式性质）→ **一致性复核**：
  换一个独立的复核提示让 Hy3 重新推导并尝试证伪，双方一致才 verified。

三态语义（与 schema.Tier2Entry 对齐，勿混淆）：
- `verified`   ：参与 G0 判定
- `unverified` ：不参与判定，G0 返回 NE（**不是通过**）
- `quarantined`：校验发现错误，隔离，永不参与判定

用法（仓库根目录）：
  PYTHONPATH=src python -m edu_eval.kb.assertions --selftest   # 校验器自检（零 LLM）
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import sympy
from sympy.parsing.sympy_parser import (
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

from ..eval.rules import _CJK_RE, normalize_math, verify_equation
from .schema import Tier2Entry

_TRANSFORMS = standard_transformations + (implicit_multiplication_application,)

#: 13 课题清单（docs/kb_scope.md §2，勿擅自改动；改了要同步文档）
TOPICS: List[Dict[str, Any]] = [
    {"no": 1, "name": "有理数运算", "grade": "七年级上册", "budget": (10, 15)},
    {"no": 2, "name": "整式的加减", "grade": "七年级上册", "budget": (8, 10)},
    {"no": 3, "name": "一元一次方程", "grade": "七年级上册", "budget": (10, 15)},
    {"no": 4, "name": "二元一次方程组", "grade": "七年级下册", "budget": (10, 12)},
    {"no": 5, "name": "全等三角形", "grade": "八年级上册", "budget": (10, 15)},
    {"no": 6, "name": "整式的乘法与因式分解", "grade": "八年级上册", "budget": (10, 15)},
    {"no": 7, "name": "分式", "grade": "八年级上册", "budget": (10, 12)},
    {"no": 8, "name": "勾股定理", "grade": "八年级下册", "budget": (8, 12)},
    {"no": 9, "name": "一次函数", "grade": "八年级下册", "budget": (10, 15)},
    {"no": 10, "name": "一元二次方程", "grade": "九年级上册", "budget": (10, 15)},
    {"no": 11, "name": "二次函数", "grade": "九年级上册", "budget": (12, 15)},
    {"no": 12, "name": "相似三角形", "grade": "九年级下册", "budget": (10, 12)},
    {"no": 13, "name": "锐角三角函数", "grade": "九年级下册", "budget": (8, 12)},
]

#: 断言校验方式
METHOD_SYMPY_IDENTITY = "sympy_identity"
METHOD_SYMPY_SOLVE = "sympy_solve"
METHOD_NUMERIC = "numeric_sampling"
METHOD_CONSENSUS = "llm_consensus"
METHOD_NONE = "none"

SOURCE_SELF = "EduEval 自建断言集（Hy3 生成 + 确定性校验）"
LICENSE_SELF = "MIT"
VERSION_SELF = "tier2-v1"


def topic_by_no(no: int) -> Dict[str, Any]:
    for t in TOPICS:
        if t["no"] == no:
            return t
    raise KeyError(f"未知课题编号：{no}（合法范围 1–13）")


def topic_by_name(name: str) -> Dict[str, Any]:
    for t in TOPICS:
        if t["name"] == name:
            return t
    raise KeyError(f"未知课题：{name}")


# ---------------------------------------------------------------- 生成提示
GEN_SYSTEM = (
    "你是初中数学教研员，正在为「AI 生成教学设计」的自动评测构建可核验知识断言库。"
    "你输出的每条断言都将被计算机程序用符号计算（sympy）严格校验，"
    "错误断言会被立即丢弃，因此宁可少写、绝不写不确定的内容。"
)


def gen_user_prompt(topic: Dict[str, Any], max_items: Optional[int] = None) -> str:
    """生成候选断言的提示词。

    `max_items` 用于**降级分批**：hy3 是推理模型，输出条数越多思维链越长，
    内容量大的课题（如一元二次方程）会稳定超出输出预算导致 JSON 被截断。
    此时改用小批量（如 6 条）重试即可恢复。
    """
    lo, hi = topic["budget"]
    if max_items:
        lo = min(lo, max_items)
        hi = min(hi, max_items)
    return (
        f"课题：{topic['name']}（{topic['grade']}，人教版）\n"
        f"请输出该课题的 {lo}–{hi} 条**核心可核验断言**，覆盖定义、公式与恒等式、"
        "定理与判定条件、典型解法与易错点四类。\n\n"
        "每条断言必须包含以下字段：\n"
        "- assertion：中文自然语言断言，一句话，必须可判断对错\n"
        "- formula：sympy 可解析的数学表达式（用 ** 表示乘方、* 表示乘法）；"
        "恒等式写成「左边 = 右边」，求根类写成「方程 = 0」；"
        "无对应数学式的结构性断言填空字符串\n"
        "- kind：identity（恒等式/公式）｜ solve（求根/求解）｜ numeric（数值关系）"
        "｜ structural（结构性，无公式）\n"
        "- solutions：仅 kind=solve 时填，方程的解（字符串数组，如 [\"2\", \"3\"]）；"
        "其余填空数组\n"
        "- conditions：适用条件（如 \"a ≠ 0\"），无条件填空字符串\n"
        "- common_errors：常见错误写法数组（1–3 条），没有填空数组\n"
        "- concepts：涉及的初中概念名数组（1–3 个）\n"
        "- std_ref：《义务教育数学课程标准（2022 年版）》对应内容要求，"
        "不确定填空字符串\n\n"
        "严格要求：\n"
        "1. 只输出 JSON，键为 \"assertions\"，值为上述对象数组；不要任何额外说明\n"
        "2. 数学式必须能被 sympy 直接解析：不要出现中文、不要使用 ^ 表示乘方\n"
        "3. 断言必须是本课题的**核心**结论，不要出偏题、不要出高中内容\n"
        "4. 不确定的公式宁可不写，也不要猜测\n"
    )


CONSENSUS_SYSTEM = (
    "你是初中数学审校员。下面是一批待核实的数学断言，你要逐条**独立重新推导**并"
    "尝试证伪（找反例、查适用条件、比对教材通行表述）。只有你确认无误的才能判为正确；"
    "有任何不确定就判为错误——被误放行的错误断言会导致教学评测系统性出错。"
)


def consensus_user_prompt(topic: Dict[str, Any], candidates: List[dict]) -> str:
    items = [
        {"idx": i, "assertion": c.get("assertion", ""),
         "formula": c.get("formula", ""), "conditions": c.get("conditions", "")}
        for i, c in enumerate(candidates)
    ]
    return (
        f"课题：{topic['name']}（{topic['grade']}，人教版）\n"
        "请逐条核实下列断言：\n"
        + json.dumps(items, ensure_ascii=False, indent=1)
        + "\n\n只输出 JSON：{\"verdicts\":[{\"idx\":0,\"correct\":true,"
          "\"reason\":\"一句话理由\"}]}，idx 必须与原数组一一对应，不要遗漏。"
    )


# ---------------------------------------------------------------- 解析
def parse_candidates(raw: str) -> List[dict]:
    """宽容解析 Hy3 生成的候选断言列表。

    容忍三种形态：{"assertions":[...]} / 直接是数组 / 被 prose 包裹的 JSON。
    解析不出任何条目时返回空列表（由调用方决定是否重试）。
    """
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}|\[.*\]", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):
        data = data.get("assertions") or data.get("items") or []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict) and c.get("assertion")]


def parse_verdicts(raw: str,
                   n_items: Optional[int] = None) -> Dict[int, Tuple[bool, str]]:
    """解析一致性复核结果 → {idx: (correct, reason)}。

    `n_items` 给定时会做**下标基线纠正**：模型偶发用 1-based 写 idx
    （1..N 而非 0..N-1），此时整体左移一位，否则第 0 条永远匹配不上。
    """
    data: Any = None
    text = (raw or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                data = json.loads(m.group(0))
            except json.JSONDecodeError:
                return {}
        else:
            return {}
    if isinstance(data, dict):
        data = data.get("verdicts") or []
    out: Dict[int, Tuple[bool, str]] = {}
    for v in data or []:
        if not isinstance(v, dict):
            continue
        try:
            idx = int(v.get("idx"))
        except (TypeError, ValueError):
            continue
        out[idx] = (bool(v.get("correct")), str(v.get("reason", "")))
    if n_items and out and min(out) == 1 and max(out) == n_items:
        out = {i - 1: val for i, val in out.items()}
    return out


# ---------------------------------------------------------------- 校验
def verify_solve(formula: str, solutions: List[str]) -> Tuple[str, str]:
    """求根类断言校验：方程的解是否与声明一致。

    返回 (status, detail)：verified / quarantined / unverified。
    """
    norm = normalize_math(formula or "")
    if not norm or _CJK_RE.search(norm):
        return "unverified", "公式为空或含中文，无法符号求解"
    lhs, _, rhs = norm.partition("=")
    if not lhs or not rhs:
        return "unverified", "不是等式形式（如 x**2-4=0）"
    try:
        expr = parse_expr(lhs, transformations=_TRANSFORMS) - parse_expr(
            rhs, transformations=_TRANSFORMS)
        expected = [parse_expr(normalize_math(s), transformations=_TRANSFORMS)
                    for s in solutions if str(s).strip()]
    except Exception as e:
        return "unverified", f"不可解析：{type(e).__name__}"
    if not expected:
        return "unverified", "kind=solve 但未给出 solutions"
    free = sorted(expr.free_symbols, key=str)
    if len(free) != 1:
        return "unverified", f"求解对象应为单变量方程，当前变量：{free}"
    try:
        got = sympy.solve(expr, free[0])
    except Exception as e:
        return "unverified", f"求解失败：{type(e).__name__}"
    same = len(got) == len(expected) and all(
        any(sympy.simplify(g - e) == 0 for e in expected) for g in got)
    return ("verified", f"解集一致：{got}") if same else (
        "quarantined", f"解集不一致：声明 {expected}，实算 {got}")


def verify_candidate(cand: dict) -> Tuple[str, str, str]:
    """单条候选断言的确定性校验 → (method, status, detail)。

    分派规则：
    - kind=solve → sympy 求解比对解集
    - formula 非空且为等式 → 复用规则层 verify_equation（恒等/数值采样/反例）
    - 其余（structural 或无可算公式）→ 交给一致性复核，此处标记待复核
    """
    kind = str(cand.get("kind") or "").strip().lower()
    formula = str(cand.get("formula") or "").strip()
    if kind == "solve":
        status, detail = verify_solve(formula, list(cand.get("solutions") or []))
        return METHOD_SYMPY_SOLVE, status, detail
    if formula and "=" in formula:
        chk = verify_equation(formula)
        method = METHOD_SYMPY_IDENTITY if "符号验证" in chk.reason else METHOD_NUMERIC
        if chk.verdict == "pass":
            status = "verified"
        elif chk.verdict == "fail":
            status = "quarantined"
        else:
            status = "unverified"
        return method, status, chk.reason
    return METHOD_NONE, "unverified", "无 sympy 可算公式，需一致性复核"


def apply_consensus(cand: dict,
                    verdict: Optional[Tuple[bool, str]]) -> Tuple[str, str, str]:
    """一致性复核结论落定为最终状态。

    三态必须分清（首轮实测踩坑）：
    - 复核判正确 → verified
    - 复核判错误 → quarantined
    - **复核没返回该条结论 → unverified（NE，未判定），绝不是 quarantined**
      把「没拿到结论」当成「断言有错」会一次性误杀整批断言，且方向上
      更恶劣：quarantined 是**断言其错误**的强断言，unverified 只是能力边界。
    """
    if verdict is None:
        return METHOD_CONSENSUS, "unverified", "一致性复核未返回该条结论（NE）"
    correct, reason = verdict
    if correct:
        return METHOD_CONSENSUS, "verified", f"一致性复核通过：{reason}"
    return METHOD_CONSENSUS, "quarantined", f"一致性复核否决：{reason}"


# ---------------------------------------------------------------- 条目构造
def make_entry(cand: dict, topic: Dict[str, Any], seq: int,
               method: str, status: str, detail: str) -> dict:
    """构造 Tier2 条目原始 dict（随后由 schema.Tier2Entry 校验）。"""
    from datetime import datetime, timezone

    eid = f"as-{topic['no']:02d}-{seq:03d}"
    return {
        "id": eid,
        "topic": topic["name"],
        "tier": 2,
        "grade": topic["grade"],
        "assertion": str(cand.get("assertion", "")).strip(),
        "formula": str(cand.get("formula", "") or "").strip(),
        "conditions": str(cand.get("conditions", "") or "").strip(),
        "common_errors": [str(x) for x in (cand.get("common_errors") or []) if x],
        "verification": {
            "method": method,
            "status": status,
            "detail": detail,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        "std_ref": str(cand.get("std_ref", "") or "").strip(),
        "concepts": [str(x) for x in (cand.get("concepts") or []) if x],
        "source": SOURCE_SELF,
        "license": LICENSE_SELF,
        "version": VERSION_SELF,
        "quarantined": False,
    }


def validate_entry(entry: dict) -> List[str]:
    errs = Tier2Entry.from_dict(entry).validate()
    if entry.get("verification", {}).get("status") not in (
            "verified", "unverified", "quarantined"):
        errs.append(f"{entry.get('id')}: verification.status 非法")
    return errs


def _selftest() -> None:
    """校验器自检（零 LLM）：正例/反例/不可算三类必须分得开。"""
    cases = [
        ({"kind": "identity", "formula": "(a+b)**2 = a**2+2*a*b+b**2"},
         "verified", "正确恒等式"),
        ({"kind": "identity", "formula": "(a+b)**2 = a**2+b**2"},
         "quarantined", "漏交叉项（典型错误）"),
        ({"kind": "solve", "formula": "x**2-5*x+6 = 0", "solutions": ["2", "3"]},
         "verified", "求根正确"),
        ({"kind": "solve", "formula": "x**2-5*x+6 = 0", "solutions": ["1", "5"]},
         "quarantined", "求根错误"),
        ({"kind": "structural", "formula": "", "assertion": "SSS 可判定全等"},
         "unverified", "结构性断言待复核"),
    ]
    ok = True
    for cand, expect, label in cases:
        method, status, detail = verify_candidate(cand)
        flag = "✓" if status == expect else "✗"
        ok = ok and status == expect
        print(f"{flag} [{label}] {method} → {status}（期望 {expect}）｜{detail[:50]}")
    print("\n自检" + ("通过" if ok else "失败"))


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
