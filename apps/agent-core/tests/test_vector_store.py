from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from app.rag.chunking import KnowledgeChunk
from app.rag.embedding import EmbeddedKnowledgeChunk
from app.rag.vector_store import (
    SemanticSearchResult,
    search_knowledge_chunks_by_keyword,
    search_similar_knowledge_chunks,
    store_embedded_knowledge_chunks,
    try_store_embedded_knowledge_chunk,
)


class FakeVectorStoreResult:
    def __init__(self, returned_id: str | None) -> None:
        self._returned_id = returned_id

    def scalar_one_or_none(self) -> str | None:
        return self._returned_id


class FakeVectorStoreConnection:
    def __init__(
        self,
        outcome: str | None | BaseException,
    ) -> None:
        self._outcome = outcome
        self.execution: tuple[TextClause, dict[str, object]] | None = None

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeVectorStoreResult:
        self.execution = (statement, parameters)

        if isinstance(self._outcome, BaseException):
            raise self._outcome

        return FakeVectorStoreResult(self._outcome)


class FakeVectorStoreTransaction:
    def __init__(self, connection: FakeVectorStoreConnection) -> None:
        self._connection = connection
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> FakeVectorStoreConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exit_exception_type = exc_type


class FakeVectorStoreEngine:
    def __init__(self, outcome: str | None | BaseException) -> None:
        self.connection = FakeVectorStoreConnection(outcome)
        self.transaction = FakeVectorStoreTransaction(self.connection)
        self.begin_calls = 0

    def begin(self) -> FakeVectorStoreTransaction:
        self.begin_calls += 1
        return self.transaction


class FakeBatchVectorStoreConnection:
    def __init__(self, outcomes: list[str | None | BaseException]) -> None:
        self._outcomes = outcomes.copy()
        self.executions: list[tuple[TextClause, dict[str, object]]] = []

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeVectorStoreResult:
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
    def __init__(self, outcomes: list[str | None | BaseException]) -> None:
        self.connection = FakeBatchVectorStoreConnection(outcomes)
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
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> FakeSearchMappings:
        return FakeSearchMappings(self._rows)


class FakeSearchConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.execution: tuple[TextClause, dict[str, object]] | None = None

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeSearchResult:
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
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.connection = FakeSearchConnection(rows)
        self.context = FakeSearchContext(self.connection)
        self.connect_calls = 0

    def connect(self) -> FakeSearchContext:
        self.connect_calls += 1
        return self.context


def make_embedded_chunk() -> EmbeddedKnowledgeChunk:
    return EmbeddedKnowledgeChunk(
        chunk=KnowledgeChunk(
            chunk_id="a" * 64,
            source_id="refund-policy-v1",
            page_number=2,
            chunk_index=0,
            content="退款申请需由订单本人提交。",
        ),
        embedding=[0.1, 0.2, 0.3],
    )


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
        )
        for index, character in enumerate(("a", "b", "c"))
    ]


async def test_try_store_embedded_chunk_inserts_pgvector_parameters() -> None:
    embedded_chunk = make_embedded_chunk()
    fake_engine = FakeVectorStoreEngine(embedded_chunk.chunk.chunk_id)

    stored = await try_store_embedded_knowledge_chunk(
        cast(AsyncEngine, fake_engine),
        embedded_chunk,
    )

    assert stored is True
    assert fake_engine.begin_calls == 1
    assert fake_engine.transaction.exit_exception_type is None
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "INSERT INTO agent_core.knowledge_chunks" in sql
    assert "ON CONFLICT (chunk_id) DO NOTHING" in sql
    assert "RETURNING chunk_id" in sql
    assert str(statement._bindparams["embedding"].type) == "VECTOR(1536)"
    assert parameters == {
        "chunk_id": "a" * 64,
        "source_id": "refund-policy-v1",
        "page_number": 2,
        "chunk_index": 0,
        "content": "退款申请需由订单本人提交。",
        "embedding": [0.1, 0.2, 0.3],
    }


async def test_try_store_embedded_chunk_returns_false_for_existing_id() -> None:
    fake_engine = FakeVectorStoreEngine(None)

    stored = await try_store_embedded_knowledge_chunk(
        cast(AsyncEngine, fake_engine),
        make_embedded_chunk(),
    )

    assert stored is False
    assert fake_engine.transaction.exit_exception_type is None


async def test_try_store_embedded_chunk_propagates_database_failure() -> None:
    fake_engine = FakeVectorStoreEngine(RuntimeError("simulated database failure"))

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await try_store_embedded_knowledge_chunk(
            cast(AsyncEngine, fake_engine),
            make_embedded_chunk(),
        )

    assert fake_engine.transaction.exit_exception_type is RuntimeError


async def test_store_embedded_chunks_counts_new_rows_in_one_transaction() -> None:
    chunks = make_embedded_chunks()
    fake_engine = FakeBatchVectorStoreEngine(
        [chunks[0].chunk.chunk_id, None, chunks[2].chunk.chunk_id]
    )

    stored_count = await store_embedded_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        chunks,
    )

    assert stored_count == 2
    assert fake_engine.begin_calls == 1
    assert fake_engine.transaction.exit_exception_type is None
    assert len(fake_engine.connection.executions) == 3
    assert [
        parameters["chunk_id"]
        for _, parameters in fake_engine.connection.executions
    ] == [embedded.chunk.chunk_id for embedded in chunks]
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
    query_embedding = [0.1] * 1536

    results = await search_similar_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        query_embedding,
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
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "embedding <=> :query_embedding AS distance" in sql
    assert "WHERE embedding IS NOT NULL" in sql
    assert "ORDER BY distance ASC, chunk_id ASC" in sql
    assert "LIMIT :limit" in sql
    assert str(statement._bindparams["query_embedding"].type) == "VECTOR(1536)"
    assert parameters == {"query_embedding": query_embedding, "limit": 2}


async def test_search_similar_chunks_returns_empty_list_for_no_matches() -> None:
    fake_engine = FakeSearchEngine([])

    results = await search_similar_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        [0.1] * 1536,
    )

    assert results == []
    assert fake_engine.connect_calls == 1


@pytest.mark.parametrize("limit", [0, -1])
async def test_search_similar_chunks_rejects_non_positive_limit(limit: int) -> None:
    fake_engine = FakeSearchEngine([])

    with pytest.raises(ValueError, match="limit must be greater than zero"):
        await search_similar_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            [0.1, 0.2, 0.3],
            limit=limit,
        )

    assert fake_engine.connect_calls == 0


async def test_search_similar_chunks_rejects_wrong_query_vector_dimension() -> None:
    fake_engine = FakeSearchEngine([])

    with pytest.raises(ValueError, match="embedding dimension must be 1536"):
        await search_similar_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            [0.1, 0.2, 0.3],
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
