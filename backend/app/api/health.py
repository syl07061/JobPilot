from fastapi import APIRouter

from app import __version__
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """只验证本服务可响应，不检查 BossHunter、数据库或模型服务。"""
    return HealthResponse(status="ok", version=__version__)
