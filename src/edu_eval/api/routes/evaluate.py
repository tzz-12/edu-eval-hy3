"""评测端点。

POST /api/evaluate：同步评测（30-90s），返回完整 Report。
支持两种入参：
- source='live'（默认）：真实调 API 跑 evaluate_from_text
- source='demo:<id>'：返回 data/demo_reports/<id>.json 预生成报告（秒级）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ...config import Hy3Config
from ...eval.run_eval import EvalContext, evaluate_from_text
from ..models import EvaluateRequest
from ..storage import add_report
import sympy

def _json_default(o):
    """sympy Integer/Float 等非原生类型 → 原生 Python 值，供 FastAPI 序列化。"""
    try:
        if isinstance(o, sympy.Integer):  return int(o)
        if isinstance(o, sympy.Float):    return float(o)
        if isinstance(o, sympy.BoolAtom): return bool(o)
    except ImportError:
        pass
    if hasattr(o, "is_integer") and o.is_integer:
        return int(o)
    if hasattr(o, "as_real_imag"):
        return float(o)
    if isinstance(o, (set, frozenset)):
        return list(o)
    return str(o)


def _sanitize_payload(obj):
    """深清洗：递归把 sympy Integer/Float/Bool 等非原生类型转 Python 原生值，
    供 FastAPI JSONResponse 序列化。jsonable_encoder 本身只认标准类型。"""
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_payload(v) for v in obj]
    try:
        import sympy
        if isinstance(obj, (sympy.Integer, sympy.BoolAtom)):
            return int(obj) if isinstance(obj, sympy.Integer) else bool(obj)
        if isinstance(obj, sympy.Float):
            return float(obj)
        if isinstance(obj, sympy.Basic):
            return str(obj)
    except ImportError:
        pass
    # 最后兜底：可调用 str() 的都字符串化
    try:
        return str(obj)
    except Exception:
        return None


router = APIRouter()
log = logging.getLogger(__name__)

DEMO_DIR = Path(os.environ.get("EDU_EVAL_DEMO_DIR", "data/demo_reports"))


def _truncate(text: str, n: int = 60) -> str:
    t = text.strip().replace("\n", " ")
    return t[:n] + ("…" if len(t) > n else "")


@router.post("/api/evaluate")
def evaluate(req: EvaluateRequest) -> Dict[str, Any]:
    # 真实跑时 text 不能为空（demo 入口由预生成报告兜底）
    if not (req.source and req.source.startswith("demo:")):
        if not req.text or not req.text.strip():
            raise HTTPException(400, "非 demo 模式下 text 必填")

    # ---- 演示快捷入口：读预生成报告 ----
    if req.source and req.source.startswith("demo:"):
        demo_id = req.source.split(":", 1)[1]
        if "/" in demo_id or ".." in demo_id:
            raise HTTPException(400, "非法 demo id")
        path = DEMO_DIR / f"{demo_id}.json"
        if not path.exists():
            raise HTTPException(
                404, f"演示报告不存在：{demo_id}。"
                     f"先用 `python scripts/pregen_demo_reports.py` 生成。"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_source"] = f"demo:{demo_id}"
        # 演示报告不入历史库（避免污染）
        return _sanitize_payload(payload)

    # ---- 真实跑：调 evaluate_from_text ----
    key = os.environ.get("HY3_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            503, "未配置 HY3_API_KEY。请先 set -a; source .env; set +a 再启动服务。"
        )

    try:
        cfg = Hy3Config.from_env()
        ctx = EvalContext(grade=req.grade)
        report = evaluate_from_text(
            req.text, cfg, ctx,
            dual_sample=req.dual_sample,
            dual_threshold=req.dual_threshold if req.dual_threshold is not None else 1,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("evaluate failed")
        raise HTTPException(500, f"评测失败：{type(e).__name__}: {str(e)[:300]}")

    payload = _sanitize_payload(report.to_dict())
    payload["_source"] = "live"
    try:
        rid = add_report(
            payload,
            file_name=_truncate(req.text),
            grade=req.grade,
            dual_sample=req.dual_sample,
        )
        payload["_id"] = rid
    except Exception as e:
        # 持久化失败不阻断返回（评测结果才是核心）
        log.warning("写入历史失败：%s", e)
    return payload

