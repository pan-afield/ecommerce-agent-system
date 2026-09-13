from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.api.dependencies import get_current_user_id
from app.api.serializers import format_citations
from app.core.redis import (
    build_rag_cache_key,
    get_cache_value,
    set_cache_value,
)
from app.rag.local_embeddings import RagEmbeddingError
from app.rag.service import build_rag_context
from app.rag.vector_store import RagVectorDimensionError
from app.schemas.error import ErrorDetail, ErrorResponse
from app.schemas.rag import KnowledgeCitationResponse

router = APIRouter(prefix="/v1/rag", tags=["rag"])


class RagSearchResponse(BaseModel):
    query: str
    citations: list[KnowledgeCitationResponse]


def _rag_error_response(
    status_code: int,
    code: Literal[
        "rag_not_configured",
        "rag_invalid_query",
        "rag_embedding_unavailable",
        "rag_database_incompatible",
    ],
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
    cache_key: str | None = None
    engine = request.app.state.database_engine
    embeddings = request.app.state.rag_embeddings
    embedding_model = request.app.state.settings.rag_embedding_model
    redis_client = getattr(request.app.state, "redis_client", None)
    settings = request.app.state.settings

    if embeddings is None:
        return _rag_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="rag_not_configured",
            message="知识库检索服务尚未配置。",
        )

    try:
        if redis_client is not None:
            cache_key = build_rag_cache_key(
                query,
                embedding_model=settings.rag_embedding_model,
                embedding_dimensions=settings.rag_embedding_dimensions,
                limit=limit,
            )
            cached_value = await get_cache_value(redis_client, cache_key)

            if cached_value is not None:
                try:
                    return RagSearchResponse.model_validate(cached_value)
                except ValidationError:
                    pass
        context = await build_rag_context(
            engine, embeddings, query, limit=limit, embedding_model=embedding_model
        )
    except RagEmbeddingError:
        return _rag_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="rag_embedding_unavailable",
            message="知识库向量服务暂时不可用。",
        )
    except RagVectorDimensionError:
        return _rag_error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="rag_database_incompatible",
            message="知识库向量数据库配置不兼容。",
        )
    except ValueError:
        return _rag_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="rag_invalid_query",
            message="知识库查询参数无效。",
        )

    response = RagSearchResponse(
        query=context.query,
        citations=format_citations(context.citations),
    )

    if redis_client is not None and cache_key is not None:
        await set_cache_value(
            redis_client,
            cache_key,
            response.model_dump(mode="json"),
            ttl_seconds=settings.redis_cache_ttl_seconds,
        )

    return response
