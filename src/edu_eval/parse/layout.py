"""版面/结构提取：公式标记、章节层级、页序归一。

与格式无关的文本结构分析，被 parsers.py 复用：
- extract_formula_marks：从文本抽取数学公式/等式片段（供规则层与 Judge 引用）
- build_outline：把解析出的行归一为 (级别, 文本) 层级序列
"""
from __future__ import annotations

import re
from typing import List, Tuple

# 等式片段（与 rules.extract_equations 同一套正则，但只做抽取不做判定）
_EQ = re.compile(
    r"[0-9a-zA-Z√（(][0-9a-zA-Z√()（）+\-−×÷·²³^ .,/]*"
    r"=[0-9a-zA-Z√()（）+\-−×÷·²³^ .,/]*"
)
# 含变量/运算的行（公式候选，无等号也计入：如 a²+b²）
_MATHLINE = re.compile(
    r"[0-9a-zA-Z()²³+\-−×÷·^/]{2,}"
)

_HEAD = re.compile(r"^#{1,6}\s+(.+)$|^([一二三四五六七八九十\d]+[、.．])\s*(.+)$")


def extract_formula_marks(text: str) -> List[str]:
    """抽取公式标记：等式优先；无等号但含数学结构的独立短行也计入。"""
    marks: List[str] = []
    seen = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for m in _EQ.finditer(line):
            frag = m.group(0).strip(" .,")
            if frag not in seen:
                seen.add(frag)
                marks.append(frag)
        # 独立数学行（整行几乎都是数学式，长度受限避免误报）
        if "=" not in line and len(line) <= 30 and _MATHLINE.fullmatch(line):
            if line not in seen:
                seen.add(line)
                marks.append(line)
    return marks


def build_outline(text: str) -> List[Tuple[int, str]]:
    """从纯文本抽取标题层级（Markdown # / 中文序号），返回 (层级, 标题)。"""
    out: List[Tuple[int, str]] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(#{1,6})\s+(.+)$", s)
        if m:
            out.append((len(m.group(1)), m.group(2).strip()))
            continue
        m = re.match(r"^([一二三四五六七八九十\d]+)[、.．]\s*(.{2,40})$", s)
        if m:
            out.append((2, f"{m.group(1)}、{m.group(2).strip()}"))
    return out


def pages_to_text(pages: List[str]) -> str:
    return "\n".join(pages)
