"""评测路由包。"""

from .health import router as health_router
from .evaluate import router as evaluate_router
from .history import router as history_router
from .manual import router as manual_router
from .source import router as source_router

__all__ = ["health_router", "evaluate_router", "history_router",
           "manual_router", "source_router"]
