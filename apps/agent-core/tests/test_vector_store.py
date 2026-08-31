from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EmbeddedKnowledgeChunk
from app.rag.vector_store import (
    DELETE_ALL_KNOWLEDGE_CHUNKS_STATEMENT,
    LOCK_EMBEDDING_MODEL_STATEMENT,
    SELECT_EMBEDDING_DIMENSION_STATEMENT,
    SELECT_EMBEDDING_MODELS_STATEMENT,
    RagEmbeddingModelConflictError,
    RagVectorDimensionError,
    SemanticSearchResult,
    replace_all_embedded_knowledge_chunks,
    search_knowledge_chunks_by_keyword,
    search_similar_knowledge_chunks,
    store_embedded_knowledge_chunks,
)


class FakeVectorStoreScalars:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def all(self) -> list[str]:
        return self._values


class FakeVectorStoreResult:
    def __init__(
        self,
        returned_id: str | None = None,
        scalar_values: list[str] | None = None,
    ) -> None:
        self._returned_id = returned_id
        self._scalar_values = scalar_values or []

    def scalar_one_or_none(self) -> str | None:
        return self._returned_id

    def scalars(self) -> FakeVectorStoreScalars:
        return FakeVectorStoreScalars(self._scalar_values)


class FakeBatchVectorStoreConnection:
    def __init__(
        self,
        outcomes: list[str | None | BaseException],
        stored_models: list[str],
    ) -> None:
        self._outcomes = outcomes.copy()
        self._stored_models = stored_models
        self.events: list[str] = []
        self.executions: list[tuple[TextClause, dict[str, object]]] = []

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object] | None = None,
    ) -> FakeVectorStoreResult:
        if statement is LOCK_EMBEDDING_MODEL_STATEMENT:
            self.events.append("lock")
            return FakeVectorStoreResult()

        if statement is SELECT_EMBEDDING_MODELS_STATEMENT:
            self.events.append("select")
            return FakeVectorStoreResult(scalar_values=self._stored_models)

        if statement is DELETE_ALL_KNOWLEDGE_CHUNKS_STATEMENT:
            self.events.append("delete")
            return FakeVectorStoreResult()

        assert parameters is not None
        self.events.append("insert")
        self.executions.append((statement, parameters))
        outcome = self._outcomes.pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        return FakeVectorStoreResult(outcome)


class FakeBatchVectorStoreTransaction:
    def __init__(self, connection: FakeBatchVectorStoreConnection) -> None:
        self._connection = connection
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> FakeBatchVectorStoreConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_exception_type = exc_type


class FakeBatchVectorStoreEngine:
    def __init__(
        self,
        outcomes: list[str | None | BaseException],
        stored_models: list[str] | None = None,
    ) -> None:
        self.connection = FakeBatchVectorStoreConnection(
            outcomes,
            stored_models or [],
        )
        self.transaction = FakeBatchVectorStoreTransaction(self.connection)
        self.begin_calls = 0

    def begin(self) -> FakeBatchVectorStoreTransaction:
        self.begin_calls += 1
        return self.transaction


class FakeSearchMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows


class FakeSearchResult:
    def __init__(
        self,
        rows: list[dict[str, object]],
        scalar_value: int | None = None,
    ) -> None:
        self._rows = rows
        self._scalar_value = scalar_value

    def mappings(self) -> FakeSearchMappings:
        return FakeSearchMappings(self._rows)

    def scalar_one_or_none(self) -> int | None:
        return self._scalar_value


class FakeSearchConnection:
    def __init__(
        self,
        rows: list[dict[str, object]],
        stored_dimensions: int | None,
    ) -> None:
        self._rows = rows
        self._stored_dimensions = stored_dimensions
        self.events: list[str] = []
        self.execution: tuple[TextClause, dict[str, object]] | None = None

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object] | None = None,
    ) -> FakeSearchResult:
        if statement is SELECT_EMBEDDING_DIMENSION_STATEMENT:
            self.events.append("dimension")
            return FakeSearchResult([], scalar_value=self._stored_dimensions)

        assert parameters is not None
        self.events.append("search")
        self.execution = (statement, parameters)
        return FakeSearchResult(self._rows)


class FakeSearchContext:
    def __init__(self, connection: FakeSearchConnection) -> None:
        self._connection = connection
        self.exited = False

    async def __aenter__(self) -> FakeSearchConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True


class FakeSearchEngine:
    def __init__(
        self,
        rows: list[dict[str, object]],
        stored_dimensions: int | None = 1024,
    ) -> None:
        self.connection = FakeSearchConnection(rows, stored_dimensions)
        self.context = FakeSearchContext(self.connection)
        self.connect_calls = 0

    def connect(self) -> FakeSearchContext:
        self.connect_calls += 1
        return self.context


def make_embedded_chunks() -> list[EmbeddedKnowledgeChunk]:
    return [
        EmbeddedKnowledgeChunk(
            chunk=KnowledgeChunk(
                chunk_id=character * 64,
                source_id="refund-policy-v1",
                page_number=2,
                chunk_index=index,
                content=f"政策段落 {index}。",
            ),
            embedding=[float(index), 0.2, 0.3],
            embedding_model="test-model",
        )
        for index, character in enumerate(("a", "b", "c"))
    ]


async def test_store_embedded_chunks_counts_new_rows_in_one_transaction() -> None:
    chunks = make_embedded_chunks()
    fake_engine = FakeBatchVectorStoreEngine(
        [chunks[0].chunk.chunk_id, None, chunks[2].chunk.chunk_id],
        stored_models=["test-model"],
    )

    stored_count = await store_embedded_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        chunks,
    )

    assert stored_count == 2
    assert fake_engine.begin_calls == 1
    assert fake_engine.transaction.exit_exception_type is None
    assert fake_engine.connection.events == [
        "lock",
        "select",
        "insert",
        "insert",
        "insert",
    ]
    assert len(fake_engine.connection.executions) == 3
    assert [
        parameters["chunk_id"]
        for _, parameters in fake_engine.connection.executions
    ] == [embedded.chunk.chunk_id for embedded in chunks]
    assert [
        parameters["embedding_model"]
        for _, parameters in fake_engine.connection.executions
    ] == [embedded.embedding_model for embedded in chunks]
    assert all(
        isinstance(parameters, dict)
        for _, parameters in fake_engine.connection.executions
    )


async def test_store_embedded_chunks_skips_transaction_for_empty_input() -> None:
    fake_engine = FakeBatchVectorStoreEngine([])

    stored_count = await store_embedded_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        [],
    )

    assert stored_count == 0
    assert fake_engine.begin_calls == 0


async def test_store_embedded_chunks_rejects_mixed_models_before_transaction() -> None:
    chunks = make_embedded_chunks()
    mixed_chunks = [
        chunks[0],
        EmbeddedKnowledgeChunk(
            chunk=chunks[1].chunk,
            embedding=chunks[1].embedding,
            embedding_model="other-model",
        ),
    ]
    fake_engine = FakeBatchVectorStoreEngine([])

    with pytest.raises(
        RagEmbeddingModelConflictError,
        match="one ingestion batch must use exactly one embedding model",
    ):
        await store_embedded_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            mixed_chunks,
        )

    assert fake_engine.begin_calls == 0


async def test_store_embedded_chunks_rejects_model_conflict_inside_transaction() -> None:
    fake_engine = FakeBatchVectorStoreEngine(
        [],
        stored_models=["previous-model"],
    )

    with pytest.raises(
        RagEmbeddingModelConflictError,
        match="stored embeddings use a different embedding model",
    ):
        await store_embedded_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            make_embedded_chunks(),
        )

    assert fake_engine.begin_calls == 1
    assert fake_engine.connection.events == ["lock", "select"]
    assert fake_engine.connection.executions == []
    assert (
        fake_engine.transaction.exit_exception_type
        is RagEmbeddingModelConflictError
    )


async def test_replace_all_embedded_chunks_rejects_empty_batch() -> None:
    fake_engine = FakeBatchVectorStoreEngine([])

    with pytest.raises(ValueError, match="replacement chunks must not be empty"):
        await replace_all_embedded_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            [],
        )

    assert fake_engine.begin_calls == 0


async def test_replace_all_embedded_chunks_rejects_mixed_models() -> None:
    chunks = make_embedded_chunks()
    mixed_chunks = [
        chunks[0],
        EmbeddedKnowledgeChunk(
            chunk=chunks[1].chunk,
            embedding=chunks[1].embedding,
            embedding_model="other-model",
        ),
    ]
    fake_engine = FakeBatchVectorStoreEngine([])

    with pytest.raises(
        RagEmbeddingModelConflictError,
        match="one replacement batch must use exactly one embedding model",
    ):
        await replace_all_embedded_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            mixed_chunks,
        )

    assert fake_engine.begin_calls == 0


async def test_replace_all_embedded_chunks_deletes_then_inserts_atomically() -> None:
    chunks = make_embedded_chunks()
    fake_engine = FakeBatchVectorStoreEngine(
        [chunk.chunk.chunk_id for chunk in chunks],
    )

    replaced_count = await replace_all_embedded_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        chunks,
    )

    assert replaced_count == 3
    assert fake_engine.connection.events == [
        "lock",
        "delete",
        "insert",
        "insert",
        "insert",
    ]
    assert fake_engine.transaction.exit_exception_type is None


async def test_replace_all_embedded_chunks_rolls_back_delete_on_insert_failure() -> None:
    chunks = make_embedded_chunks()
    fake_engine = FakeBatchVectorStoreEngine(
        [chunks[0].chunk.chunk_id, RuntimeError("simulated replacement failure")],
    )

    with pytest.raises(RuntimeError, match="simulated replacement failure"):
        await replace_all_embedded_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            chunks,
        )

    assert fake_engine.connection.events == [
        "lock",
        "delete",
        "insert",
        "insert",
    ]
    assert fake_engine.transaction.exit_exception_type is RuntimeError


async def test_store_embedded_chunks_propagates_mid_batch_failure() -> None:
    chunks = make_embedded_chunks()
    fake_engine = FakeBatchVectorStoreEngine(
        [chunks[0].chunk.chunk_id, RuntimeError("simulated database failure")]
    )

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await store_embedded_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            chunks,
        )

    assert fake_engine.begin_calls == 1
    assert len(fake_engine.connection.executions) == 2
    assert fake_engine.transaction.exit_exception_type is RuntimeError


async def test_search_similar_chunks_maps_ranked_rows_with_stable_sql() -> None:
    rows: list[dict[str, object]] = [
        {
            "chunk_id": "a" * 64,
            "source_id": "refund-policy-v1",
            "page_number": 2,
            "chunk_index": 0,
            "content": "退款申请需由订单本人提交。",
            "distance": "0.125",
        },
        {
            "chunk_id": "b" * 64,
            "source_id": "refund-policy-v1",
            "page_number": 3,
            "chunk_index": 1,
            "content": "审批通过后进入退款处理。",
            "distance": 0.25,
        },
    ]
    fake_engine = FakeSearchEngine(rows)
    query_embedding = [0.1] * 1024

    results = await search_similar_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        query_embedding,
        embedding_model="test-model",
        limit=2,
    )

    assert results == [
        SemanticSearchResult(
            chunk=KnowledgeChunk(
                chunk_id="a" * 64,
                source_id="refund-policy-v1",
                page_number=2,
                chunk_index=0,
                content="退款申请需由订单本人提交。",
            ),
            distance=0.125,
        ),
        SemanticSearchResult(
            chunk=KnowledgeChunk(
                chunk_id="b" * 64,
                source_id="refund-policy-v1",
                page_number=3,
                chunk_index=1,
                content="审批通过后进入退款处理。",
            ),
            distance=0.25,
        ),
    ]
    assert fake_engine.connect_calls == 1
    assert fake_engine.context.exited is True
    assert fake_engine.connection.events == ["dimension", "search"]
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "embedding <=> :query_embedding AS distance" in sql
    assert "WHERE embedding IS NOT NULL" in sql
    assert "AND embedding_model = :embedding_model" in sql
    assert "ORDER BY distance ASC, chunk_id ASC" in sql
    assert "LIMIT :limit" in sql
    assert str(statement._bindparams["query_embedding"].type) == "VECTOR(1024)"
    assert parameters == {
        "query_embedding": query_embedding,
        "embedding_model": "test-model",
        "limit": 2,
    }


async def test_search_similar_chunks_returns_empty_list_for_no_matches() -> None:
    fake_engine = FakeSearchEngine([])

    results = await search_similar_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        [0.1] * 1024,
        embedding_model="test-model",
    )

    assert results == []
    assert fake_engine.connect_calls == 1
    assert fake_engine.connection.events == ["dimension", "search"]


@pytest.mark.parametrize("stored_dimensions", [1536, None])
async def test_search_similar_chunks_rejects_incompatible_database_dimension(
    stored_dimensions: int | None,
) -> None:
    fake_engine = FakeSearchEngine([], stored_dimensions=stored_dimensions)

    with pytest.raises(
        RagVectorDimensionError,
        match=(
            "knowledge embedding column dimension mismatch: "
            f"expected 1024, got {stored_dimensions}"
        ),
    ):
        await search_similar_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            [0.1] * 1024,
            embedding_model="test-model",
        )

    assert fake_engine.connection.events == ["dimension"]
    assert fake_engine.connection.execution is None
    assert fake_engine.context.exited is True


@pytest.mark.parametrize("limit", [0, -1])
async def test_search_similar_chunks_rejects_non_positive_limit(limit: int) -> None:
    fake_engine = FakeSearchEngine([])

    with pytest.raises(ValueError, match="limit must be greater than zero"):
        await search_similar_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            [0.1, 0.2, 0.3],
            embedding_model="test-model",
            limit=limit,
        )

    assert fake_engine.connect_calls == 0


async def test_search_similar_chunks_rejects_wrong_query_vector_dimension() -> None:
    fake_engine = FakeSearchEngine([])

    with pytest.raises(ValueError, match="embedding dimension must be 1024"):
        await search_similar_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            [0.1, 0.2, 0.3],
            embedding_model="test-model",
        )

    assert fake_engine.connect_calls == 0


async def test_search_chunks_by_keyword_strips_query_and_maps_rows() -> None:
    rows: list[dict[str, object]] = [
        {
            "chunk_id": "c" * 64,
            "source_id": "refund-policy-v2",
            "page_number": 4,
            "chunk_index": 2,
            "content": "退款审核通常需要一个工作日。",
        }
    ]
    fake_engine = FakeSearchEngine(rows)

    results = await search_knowledge_chunks_by_keyword(
        cast(AsyncEngine, fake_engine),
        "  退款审核  ",
        limit=5,
    )

    assert results == [
        KnowledgeChunk(
            chunk_id="c" * 64,
            source_id="refund-policy-v2",
            page_number=4,
            chunk_index=2,
            content="退款审核通常需要一个工作日。",
        )
    ]
    assert fake_engine.connect_calls == 1
    assert fake_engine.context.exited is True
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "strpos(lower(content), lower(:query)) > 0" in sql
    assert "strpos(lower(content), lower(:query)) ASC" in sql
    assert "char_length(content) ASC" in sql
    assert "chunk_id ASC" in sql
    assert "LIMIT :limit" in sql
    assert parameters == {"query": "退款审核", "limit": 5}


async def test_search_chunks_by_keyword_returns_empty_list_for_no_matches() -> None:
    fake_engine = FakeSearchEngine([])

    results = await search_knowledge_chunks_by_keyword(
        cast(AsyncEngine, fake_engine),
        "物流",
    )

    assert results == []
    assert fake_engine.connect_calls == 1


@pytest.mark.parametrize("query", ["", "   ", "\n\t"])
async def test_search_chunks_by_keyword_rejects_blank_query(query: str) -> None:
    fake_engine = FakeSearchEngine([])

    with pytest.raises(ValueError, match="query must not be empty or whitespace"):
        await search_knowledge_chunks_by_keyword(
            cast(AsyncEngine, fake_engine),
            query,
        )

    assert fake_engine.connect_calls == 0


@pytest.mark.parametrize("limit", [0, -1])
async def test_search_chunks_by_keyword_rejects_non_positive_limit(limit: int) -> None:
    fake_engine = FakeSearchEngine([])

    with pytest.raises(ValueError, match="limit must be greater than zero"):
        await search_knowledge_chunks_by_keyword(
            cast(AsyncEngine, fake_engine),
            "退款",
            limit=limit,
        )

    assert fake_engine.connect_calls == 0
