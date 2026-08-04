from typing import Protocol, cast

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.schemas.health import HealthResponse


class ReadinessProbe(Protocol):
    async def __call__(self, engine: AsyncEngine) -> bool: ...


router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=HealthResponse)
async def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def readiness(request: Request) -> HealthResponse | JSONResponse:
    engine = cast(AsyncEngine, request.app.state.database_engine)
    probe = cast(ReadinessProbe, request.app.state.readiness_probe)

    if await probe(engine):
        return HealthResponse(status="ok", checks={"database": "ok"})

    payload = HealthResponse(status="not_ready", checks={"database": "unavailable"})
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=payload.model_dump(),
    )
