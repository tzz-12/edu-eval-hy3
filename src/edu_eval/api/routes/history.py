"""历史记录端点。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import ReportSummary
from ..storage import get_report, list_reports

router = APIRouter()


@router.get("/api/reports", response_model=list[ReportSummary])
def reports_list() -> list:
    return list_reports(limit=50)


@router.get("/api/reports/{rid}")
def report_get(rid: int) -> dict:
    r = get_report(rid)
    if r is None:
        raise HTTPException(404, "报告不存在")
    return r
