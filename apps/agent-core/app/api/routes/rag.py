from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.dependencies import get_current_user_id
from app.api.serializers import format_citations
from app.rag.service import build_rag_context
from app.schemas.error import ErrorDetail, ErrorResponse
from app.schemas.rag import KnowledgeCitationResponse

router = APIRouter(prefix="/v1/rag", tags=["rag"])


class RagSearchResponse(BaseModel):
    query: str
    citations: list[KnowledgeCitationResponse]


def _rag_error_response(
    status_code: int,
    code: Literal["rag_not_configured", "rag_invalid_query"],
    message: str,
) -> JSONResponse:
    response = ErrorResponse(
        error=ErrorDetail(code=code, message=message),
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(),
    )


@router.get(
    "/search",
    response_model=RagSearchResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
async def search_rag(
    request: Request,
    query: Annotated[
        str,
        Query(min_length=1, max_length=500),
    ],
    limit: Annotated[
        int,
        Query(ge=1, le=10),
    ] = 3,
    _current_user_id: Annotated[
        str,
        Depends(get_current_user_id),
    ] = "",
) -> RagSearchResponse | JSONResponse:
    engine = request.app.state.database_engine
    embeddings = request.app.state.rag_embeddings

    if embeddings is None:
        return _rag_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="rag_not_configured",
            message="知识库检索服务尚未配置。",
        )

    try:
        context = await build_rag_context(
            engine,
            embeddings,
            query,
            limit=limit,
        )
    except ValueError:
        return _rag_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="rag_invalid_query",
            message="知识库查询参数无效。",
        )

    return RagSearchResponse(
        query=context.query,
        citations=format_citations(context.citations),
    )
