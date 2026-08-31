from typing import Literal, Protocol, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas.health import HealthResponse


class ReadinessProbe(Protocol):
    async def __call__(self, engine: AsyncEngine) -> bool: ...


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    """只表示进程仍能响应，不检查数据库或外部模型。"""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def readiness(request: Request) -> HealthResponse | JSONResponse:
    """检查数据库及两个可选模型服务，并在数据库不可用时返回 503。"""
    engine = cast(AsyncEngine, request.app.state.database_engine)
    probe = cast(ReadinessProbe, request.app.state.readiness_probe)

    database_ready = await probe(engine)
    checks: dict[str, Literal["ok", "unavailable"]] = {
        "database": "ok" if database_ready else "unavailable",
        "rag_embeddings": ("ok" if request.app.state.rag_embeddings is not None else "unavailable"),
        "openai_chat": ("ok" if request.app.state.chat_service is not None else "unavailable"),
    }

    if database_ready:
        return HealthResponse(status="ok", checks=checks)

    payload = HealthResponse(status="not_ready", checks=checks)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(),
    )
