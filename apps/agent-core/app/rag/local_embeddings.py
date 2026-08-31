from typing import cast

from langchain_core.embeddings import Embeddings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding  # type: ignore[import-untyped]

from app.core.config import Settings


class RagEmbeddingError(RuntimeError):
    """本地 embedding 服务无法生成符合配置的向量。"""


class LocalEmbeddingsAdapter(Embeddings):
    def __init__(
        self,
        embedding: HuggingFaceEmbedding,
        dimensions: int,
    ) -> None:
        """保存已初始化的 LlamaIndex 模型和期望维度，请求期间重复复用。"""
        self._embedding = embedding
        self._dimensions = dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """同步生成文档向量；异步业务路径应优先使用 ``aembed_documents``。"""
        vectors = cast(
            list[list[float]],
            self._embedding.get_text_embedding_batch(texts),
        )
        if any(len(vector) != self._dimensions for vector in vectors):
            raise RagEmbeddingError(f"embedding dimension must be {self._dimensions}")
        return vectors

    def embed_query(self, text: str) -> list[float]:
        """同步生成查询向量；仅用于 LangChain 同步接口兼容。"""
        vector = cast(
            list[float],
            self._embedding.get_query_embedding(text),
        )
        if len(vector) != self._dimensions:
            raise RagEmbeddingError(f"embedding dimension must be {self._dimensions}")
        return vector

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """异步生成文档向量，并把模型或维度错误统一包装。"""
        try:
            vectors = cast(
                list[list[float]],
                await self._embedding.aget_text_embedding_batch(texts),
            )
        except Exception as error:
            raise RagEmbeddingError("local document embedding failed") from error
        if any(len(vector) != self._dimensions for vector in vectors):
            raise RagEmbeddingError(f"embedding dimension must be {self._dimensions}")
        return vectors

    async def aembed_query(self, text: str) -> list[float]:
        """异步生成查询向量，供 RAG 请求链使用。"""
        try:
            vector = cast(
                list[float],
                await self._embedding.aget_query_embedding(text),
            )
        except Exception as error:
            raise RagEmbeddingError("local query embedding failed") from error
        if len(vector) != self._dimensions:
            raise RagEmbeddingError(f"embedding dimension must be {self._dimensions}")
        return vector


def create_local_embeddings(settings: Settings) -> LocalEmbeddingsAdapter:
    """按配置创建一次本地模型适配器；函数本身不读取 OpenAI 凭据。"""
    cache_folder = (
        str(settings.rag_embedding_cache_dir)
        if settings.rag_embedding_cache_dir is not None
        else None
    )
    return LocalEmbeddingsAdapter(
        HuggingFaceEmbedding(
            model_name=settings.rag_embedding_model,
            cache_folder=cache_folder,
            device=settings.rag_embedding_device,
        ),
        dimensions=settings.rag_embedding_dimensions,
    )
