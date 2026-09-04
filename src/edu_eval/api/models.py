"""Pydantic 模型：HTTP 边界契约。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class EvaluateRequest(BaseModel):
    """POST /api/evaluate 请求体。

    text 字段对 demo 快捷入口（source 以 demo: 开头）非必填；
    其他场景要求至少 1 字符。"""
    text: Optional[str] = Field(
        None, description="课件文本（Markdown/纯文本）；demo 源下可省略"
    )
    grade: str = Field(..., description="年级，如 七年级/八年级/九年级")
    source: Optional[str] = Field(
        None,
        description="'live' 真实跑；'demo:<id>' 走预生成报告（秒级）"
    )
    dual_sample: bool = Field(True, description="是否启用双采样自一致")
    dual_threshold: Optional[int] = Field(
        None, ge=0, le=4,
        description="分差阈值，>此值触发层内仲裁；None=默认(1)"
    )


class HealthResponse(BaseModel):
    ok: bool
    api_key_configured: bool
    model: str
    base_url: str = ""


class ReportSummary(BaseModel):
    id: int
    created_at: float
    file_name: Optional[str] = None
    grade: Optional[str] = None
    admission: str
    dual_sample: bool
    total_score: Optional[float] = None
