from langchain_openai import OpenAIEmbeddings

from app.core.config import Settings


def create_openai_embeddings(settings: Settings) -> OpenAIEmbeddings:
    if settings.openai_api_key is None:
        raise RuntimeError("OpenAI API key is not configured.")

    return OpenAIEmbeddings(
        openai_api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
        openai_api_base=(
            str(settings.openai_base_url) if settings.openai_base_url is not None else None
        ),
        request_timeout=settings.openai_request_timeout_seconds,
        max_retries=0,
    )
