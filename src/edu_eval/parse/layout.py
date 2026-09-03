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


# ---------------------------------------------------------------- 抽取降噪
_CJK = "\u4e00-\u9fff"
#: 中文之间的**单个**空白：PDF 按字符块定位抽取时，词内被切断
#: （"教学过 程"、"对 于边长"）。两格以上是栏位分隔，不能动。
_INTRA_WORD_GAP = re.compile(rf"(?<=[{_CJK}])[ \t](?=[{_CJK}])")
#: 两格及以上空白：表单/栏位分隔（"学  科  数学"），归一为全角空格以保留结构
_COLUMN_GAP = re.compile(r"[ \t]{2,}")
_BLANK_RUN = re.compile(r"\n{3,}")
#: 全角空格两侧的空白（归一后可能残留）
_EDGE_WS = re.compile(r"[ \t]*\u3000[ \t]*")


def denoise_text(text: str) -> str:
    """PDF 抽取文本的保守降噪。

    校准实测（`docs/calibration_round2.md` §4）：维度 5（清晰度）与维度 A
    （格式规范）在**人教社示范课例**上恒被扣到 3 分，而扣分证据几乎全部是
    抽取噪声——"教学过 程"、"对 于边长"、拆行表格——而非教案本身的问题。
    不降噪的话这两个维度实际测的是「PDF 抽取质量」，会系统性低估真实教案。

    三条规则，每条都有判据，不猜：

    1. **两格以上空白 → 全角空格**：这是表单/栏位分隔（"学　科　数学
       教师姓名  韩琰"），是合法版式，不能删，只归一以便与词内断裂区分。
    2. **中文之间的单个空白 → 删除**：词内被切断的噪声。之所以只在「两格
       以上已被归一」之后才做，是为了避免把栏位分隔误当成词内断裂合并成
       "学科数学教师姓名韩琰"。
    3. **连续空行压到 2 行**：不影响语义，减少提示预算浪费。

    刻意**不做**的事：不去重字（"从从简单"看着像重复，但"常常""刚刚"是
    合法词，自动去重会误伤），不动中文与数字/字母之间的空白（可能是单位
    或公式排版，如 "360 °"）。
    """
    if not text:
        return text
    out = _COLUMN_GAP.sub("\u3000", text)
    out = _INTRA_WORD_GAP.sub("", out)
    out = _EDGE_WS.sub("\u3000", out)
    out = _BLANK_RUN.sub("\n\n", out)
    return out


def noise_stats(text: str) -> dict:
    """降噪前后的噪声量对比，供回归与报告引用。"""
    intra = len(_INTRA_WORD_GAP.findall(text))
    return {
        "chars": len(text),
        "intra_word_gaps": intra,
        "noise_ratio": round(intra / max(1, len(re.findall(f"[{_CJK}]", text))), 4),
    }
