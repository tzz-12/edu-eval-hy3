"""复核器符号归一化回归测试。

PDF 抽取会把上标渲染成普通字形（y=a(x-h)² vs y=a(x-h)2），而 Judge 证据
常引用上标形式。若 deterministic_check 不归一化，会因 ²≠2 误判「证据编造」，
触发无谓仲裁。本测试锁住该行为，并含反方向用例验证测试有效性。
"""
import sys

sys.path.insert(0, "src")

from edu_eval.eval.judges.review import ReviewJudge, _norm_math


def test_superscript_evidence_matches_plain_text():
    """证据用 ²、原文用普通 2 —— 不应判编造。"""
    text = "二次函数的顶点式是 y=a(x-h)2+k，当 a>0 时开口向上。"
    scores = {"5": {"score": 4, "ne": False,
                    "evidence": "顶点式为 y=a(x-h)²+k，a>0 时开口向上。"}}
    probs = ReviewJudge.deterministic_check(scores, text)
    assert probs == [], f"误判为编造：{probs}"


def test_caret_exponent_evidence_matches_superscript_text():
    """证据用 ^2、原文用 ² —— 跨表示也应匹配。"""
    text = "平方差公式 (a+b)² = a² + 2ab + b²。"
    scores = {"5": {"score": 5, "ne": False,
                    "evidence": "展开式为 (a+b)^2 = a^2 + 2ab + b^2。"}}
    probs = ReviewJudge.deterministic_check(scores, text)
    assert probs == [], f"误判为编造：{probs}"


def test_fabricated_formula_still_flagged():
    """证据中的公式确实原文没有 —— 仍须判编造（防止归一化误放）。"""
    text = "一次函数 y=kx+b 的图象是一条直线。"
    scores = {"5": {"score": 4, "ne": False,
                    "evidence": "这里用到了二阶导数 f''(x)=6x 求拐点。"}}
    probs = ReviewJudge.deterministic_check(scores, text)
    assert any("不存在" in p["reason"] for p in probs), f"未抓到编造：{probs}"


def test_missing_evidence_flagged():
    """无证据支撑 —— 仍须判问题。"""
    text = "任意文本。"
    scores = {"5": {"score": 3, "ne": False, "evidence": ""}}
    probs = ReviewJudge.deterministic_check(scores, text)
    assert probs and probs[0]["reason"] == "无证据支撑", f"未抓到缺失：{probs}"


def test_norm_math_baseline():
    """归一化函数本身：上标/脱字符/空白统一。"""
    assert _norm_math("y = a(x-h)² + k") == "y=a(x-h)2+k"
    assert _norm_math("y=a(x-h)^2+k") == "y=a(x-h)2+k"
    assert _norm_math("x¹⁰") == "x10"


def test_antiregression_superscript_bug_would_trigger_without_norm():
    """反方向：若实现直接拿 ² 去匹配普通 2（无归一化），必须误判——
    证明本测试真能抓到回归（删除 _norm_math 即失败）。"""
    text = "顶点式 y=a(x-h)2+k。"
    scores = {"5": {"score": 4, "ne": False, "evidence": "y=a(x-h)²+k"}}
    # 模拟旧实现：用 "".join(text.split()) 而非 _norm_math
    norm_text_old = "".join(text.split())
    spans = __import__("re").findall(r"[A-Za-z0-9+\-*/^=().,²³√π≈≠≤≥%]+",
                                    scores["5"]["evidence"])
    old_result = not any("".join(sp.split()) in norm_text_old for sp in spans)
    assert old_result is True, "旧实现居然没误判——本反方向用例失效"
    # 新实现不应误判
    assert ReviewJudge.deterministic_check(scores, text) == []
