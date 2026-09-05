"""报告 payload 的 JSON 序列化清洗。

规则层用 sympy 验证公式时会构造反例（如 x=4 → sympy.Integer），
这些对象不是 Python 原生类型，FastAPI 的 jsonable_encoder 也不认，
直接返回会 `TypeError: Object of type Integer is not JSON serializable`。

踩过的坑：早期实现用 `isinstance(obj, sympy.BoolAtom)`，但 sympy 1.14
根本没有 `sympy.BoolAtom` 这个顶层属性（它在 sympy.logic.boolalg 里），
结果一碰到 sympy 对象就 AttributeError，把整个端点打挂 —— 比不清洗还糟。
所以这里一律用 Basic 上的 `is_Number` / `is_Boolean` 鸭子类型判断，
并且整块包 try/except：任何意外都降级成字符串，绝不让序列化失败冒泡。
"""

from __future__ import annotations

from typing import Any


def sanitize(obj: Any) -> Any:
    """递归把非原生类型（sympy / set / 自定义对象）转成可 JSON 序列化的值。"""
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [sanitize(v) for v in sorted(obj, key=str)]

    try:
        import sympy
    except ImportError:  # pragma: no cover - sympy 是硬依赖，兜底而已
        sympy = None  # type: ignore[assignment]

    if sympy is not None and isinstance(obj, sympy.Basic):
        try:
            if getattr(obj, "is_Boolean", False):
                return bool(obj)
            if getattr(obj, "is_Number", False):
                if getattr(obj, "is_Integer", False):
                    return int(obj)
                return float(obj)
        except (TypeError, ValueError):  # pragma: no cover - 极端表达式
            pass
        return str(obj)

    # 最后兜底
    try:
        return str(obj)
    except Exception:  # noqa: BLE001
        return None


def json_default(o: Any) -> Any:
    """json.dumps(default=...) 用的兜底函数（与 sanitize 同策略，单层）。"""
    try:
        import sympy

        if isinstance(o, sympy.Basic):
            if getattr(o, "is_Boolean", False):
                return bool(o)
            if getattr(o, "is_Number", False):
                return int(o) if getattr(o, "is_Integer", False) else float(o)
            return str(o)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(o, (set, frozenset)):
        return sorted(o, key=str)
    try:
        return str(o)
    except Exception:  # noqa: BLE001
        return None
