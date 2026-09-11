"""EduEval Demo FastAPI 主入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import evaluate_router, health_router, history_router, manual_router
from .storage import init_db

# src/edu_eval/api/app.py -> ../../../static
STATIC_DIR = Path(__file__).resolve().parent.parent.parent.parent / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    logging.info("EduEval demo API 启动（静态文件：%s）", STATIC_DIR)
    yield


# 注意：实例必须叫 fastapi_app（不能叫 app），否则与同名的 app.py 子模块
# 在 sys.modules 里产生属性冲突，`import edu_eval.api.app` 会拿到 FastAPI 实例。
fastapi_app = FastAPI(
    title="EduEval Demo",
    description="AI 生成 K-12 课件质量评测器 —— 基于混元 Hy3",
    version="1.0.0",
    lifespan=lifespan,
)

fastapi_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

fastapi_app.include_router(health_router)
fastapi_app.include_router(evaluate_router)
fastapi_app.include_router(history_router)
fastapi_app.include_router(manual_router)


# 静态前端（挂到根路径；必须放最后，否则会覆盖 /api/*）
if STATIC_DIR.exists():
    fastapi_app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
