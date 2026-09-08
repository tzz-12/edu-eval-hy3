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
    file_name: Optional[str] = Field(
        None,
        description="来源文件名（上传文件时由前端传入）；"
                    "留空则自动取正文首个一级标题，再兜底为「粘贴文本」"
    )
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
    # 演示模式（HY3_MOCK=1）：分数由确定性画像生成，不调真实模型、不耗额度。
    # 前端据此显示「演示模式」而非「API 未配置」——后者会被误读成系统坏了。
    demo_mode: bool = False
    db_writable: bool = True
    db_path: str = ""
    db_note: str = ""
    db_count: int = 0


class ReportSummary(BaseModel):
    id: int
    created_at: float
    file_name: Optional[str] = None
    grade: Optional[str] = None
    admission: str
    dual_sample: bool
    total_score: Optional[float] = None
