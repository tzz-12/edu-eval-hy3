"""健康检查 + 静态配置端点。"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter

from ..dirs import demo_dir
from ..models import HealthResponse
from ..storage import db_status

router = APIRouter()

# 与 src/edu_eval/grade_map.py 的 GRADE_KEYS 保持一致
GRADES = [
    "七年级", "八年级", "九年级",
    "高一", "高二", "高三",
]


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """不暴露 key，仅返回是否已配置。"""
    key = os.environ.get("HY3_API_KEY", "").strip()
    db = db_status()
    return HealthResponse(
        ok=bool(key),
        api_key_configured=bool(key),
        model=os.environ.get("HY3_MODEL", "未配置"),
        base_url=os.environ.get("HY3_BASE_URL", ""),
        db_writable=db["db_writable"],
        db_path=db["db_path"],
        db_note=db["db_note"],
        db_count=db["db_count"],
    )


@router.get("/api/grades")
def grades() -> dict:
    return {"grades": GRADES}


@router.get("/api/demo-samples")
def demo_samples() -> dict:
    """列出可用的演示快捷入口（与 data/demo_reports/ 下的文件名对应）。"""
    import json
    d = demo_dir()
    if not d.exists():
        return {"samples": []}
    samples = []
    for p in sorted(d.glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
            samples.append({
                "id": p.stem,
                "label": p.stem.replace("_", " · "),
                "admission": r.get("admission"),
                "total_score": (r.get("aggregation") or {}).get("total_score"),
            })
        except Exception:
            pass
    return {"samples": samples}
