"""量规访问层：维度 → 分档描述/证据的渲染辅助（供报告页与 Judge 复用）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from edu_eval.eval import dimensions as D


def dimension_table() -> List[Dict[str, Any]]:
    """全部维度的展示模型（卡片渲染用）。"""
    rows = []
    for dim in D.DIMENSIONS:
        rows.append({
            "id": dim.id,
            "name": dim.name,
            "priority": dim.priority,
            "weight": dim.weight,
            "description": dim.description,
            "redline": dim.redline,
            "auxiliary": dim.auxiliary,
            "in_total": dim.in_total,
        })
    return rows


def score_band(score: Optional[float]) -> Dict[str, str]:
    """分数 → 颜色档位（报告页视觉分级）。"""
    if score is None:
        return {"label": "NE", "color": "#8b949e", "bg": "#f6f8fa"}
    if score >= 4.5:
        return {"label": "优秀", "color": "#1a7f37", "bg": "#dafbe1"}
    if score >= 3.5:
        return {"label": "良好", "color": "#0969da", "bg": "#ddf4ff"}
    if score >= 2.5:
        return {"label": "合格", "color": "#9a6700", "bg": "#fff8c5"}
    return {"label": "待改进", "color": "#cf222e", "bg": "#ffebe9"}


def admission_badge(admission: str, redline: bool) -> Dict[str, str]:
    if redline:
        return {"label": "红线不通过", "color": "#fff", "bg": "#cf222e"}
    if admission == "PASS":
        return {"label": "准入通过", "color": "#fff", "bg": "#1a7f37"}
    if admission == "FAIL":
        return {"label": "准入不通过", "color": "#fff", "bg": "#cf222e"}
    return {"label": "暂不可评 NE", "color": "#fff", "bg": "#8b949e"}


def radar_svg(scores: Dict[str, Any], size: int = 260) -> str:
    """8 加权维度雷达图（纯 SVG，后端渲染，无前端依赖）。"""
    dims = [d for d in D.DIMENSIONS if d.in_total]
    n = len(dims)
    if n < 3:
        return ""
    cx = cy = size / 2
    r = size / 2 - 46  # 留标签空间
    import math

    def pt(i: int, frac: float):
        ang = -math.pi / 2 + 2 * math.pi * i / n
        return cx + r * frac * math.cos(ang), cy + r * frac * math.sin(ang)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
             f'height="{size}" viewBox="0 0 {size} {size}">']
    # 网格环
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                       (pt(i, frac) for i in range(n)))
        parts.append(f'<polygon points="{pts}" fill="none" stroke="#d0d7de" '
                     f'stroke-width="1"/>')
    # 轴与标签
    for i, dim in enumerate(dims):
        x, y = pt(i, 1.0)
        lx, ly = pt(i, 1.18)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" '
                     f'stroke="#d0d7de" stroke-width="1"/>')
        anchor = "middle"
        if lx < cx - 8:
            anchor = "end"
        elif lx > cx + 8:
            anchor = "start"
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="10" fill="#57606a" '
            f'text-anchor="{anchor}" dominant-baseline="middle">{dim.name}</text>')
    # 数据面
    poly, dots = [], []
    for i, dim in enumerate(dims):
        s = scores.get(dim.id)
        v = (s.get("score", 0) / 5.0) if isinstance(s, dict) and not s.get("ne") else 0.0
        x, y = pt(i, max(v, 0.04))
        poly.append(f"{x:.1f},{y:.1f}")
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#0969da"/>'
                    if v > 0 else
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#8b949e"/>')
    parts.append(f'<polygon points="{" ".join(poly)}" fill="rgba(9,105,218,0.18)" '
                 f'stroke="#0969da" stroke-width="1.6"/>')
    parts.extend(dots)
    parts.append("</svg>")
    return "".join(parts)
