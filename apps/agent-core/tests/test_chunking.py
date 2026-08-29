from hashlib import sha256
from types import TracebackType
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import TextClause

from app.rag.chunking import (
    DEFAULT_MAX_CHARS,
    KnowledgeChunk,
    chunk_policy_text,
    store_knowledge_chunks,
    try_store_knowledge_chunk,
)


class FakeStoreResult:
    def __init__(self, returned_id: str | None) -> None:
        self._returned_id = returned_id

    def scalar_one_or_none(self) -> str | None:
        return self._returned_id


class FakeStoreConnection:
    def __init__(self, returned_id: str | None) -> None:
        self._returned_id = returned_id
        self.execution: tuple[TextClause, dict[str, object]] | None = None

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeStoreResult:
        self.execution = (statement, parameters)
        return FakeStoreResult(self._returned_id)


class FakeStoreTransaction:
    def __init__(self, connection: FakeStoreConnection) -> None:
        self._connection = connection
        self.exited = False

    async def __aenter__(self) -> FakeStoreConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True


class FakeStoreEngine:
    def __init__(self, returned_id: str | None) -> None:
        self.connection = FakeStoreConnection(returned_id)
        self.transaction = FakeStoreTransaction(self.connection)

    def begin(self) -> FakeStoreTransaction:
        return self.transaction


class FakeBatchConnection:
    def __init__(self, outcomes: list[str | None | BaseException]) -> None:
        self._outcomes = outcomes.copy()
        self.executions: list[tuple[TextClause, dict[str, object]]] = []

    async def execute(
        self,
        statement: TextClause,
        parameters: dict[str, object],
    ) -> FakeStoreResult:
        self.executions.append((statement, parameters))
        outcome = self._outcomes.pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        return FakeStoreResult(outcome)


class FakeBatchTransaction:
    def __init__(self, connection: FakeBatchConnection) -> None:
        self._connection = connection
        self.exited = False
        self.exit_exception_type: type[BaseException] | None = None

    async def __aenter__(self) -> FakeBatchConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.exited = True
        self.exit_exception_type = exc_type


class FakeBatchEngine:
    def __init__(self, outcomes: list[str | None | BaseException]) -> None:
        self.connection = FakeBatchConnection(outcomes)
        self.transaction = FakeBatchTransaction(self.connection)
        self.begin_calls = 0

    def begin(self) -> FakeBatchTransaction:
        self.begin_calls += 1
        return self.transaction


def expected_chunk_id(source_id: str, chunk_index: int, content: str) -> str:
    payload = f"{source_id}\0{chunk_index}\0{content}".encode()
    return sha256(payload).hexdigest()


def expected_page_chunk_id(
    source_id: str,
    page_number: int,
    chunk_index: int,
    content: str,
) -> str:
    payload = f"{source_id}\0page:{page_number}\0{chunk_index}\0{content}".encode()
    return sha256(payload).hexdigest()


def test_chunk_policy_text_creates_traceable_paragraph_chunks() -> None:
    chunks = chunk_policy_text(
        "  refund-policy-v1  ",
        "退款申请需由订单本人提交。\n\n审批通过后进入退款处理。",
    )

    assert chunks == [
        KnowledgeChunk(
            chunk_id=expected_chunk_id(
                "refund-policy-v1",
                0,
                "退款申请需由订单本人提交。",
            ),
            source_id="refund-policy-v1",
            chunk_index=0,
            content="退款申请需由订单本人提交。",
        ),
        KnowledgeChunk(
            chunk_id=expected_chunk_id(
                "refund-policy-v1",
                1,
                "审批通过后进入退款处理。",
            ),
            source_id="refund-policy-v1",
            chunk_index=1,
            content="审批通过后进入退款处理。",
        ),
    ]


def test_chunk_policy_text_is_stable_across_line_endings() -> None:
    linux = chunk_policy_text("policy-v1", "第一段。\n\n第二段。")
    windows = chunk_policy_text("policy-v1", "第一段。\r\n\r\n第二段。")
    old_mac = chunk_policy_text("policy-v1", "第一段。\r\r第二段。")

    assert windows == linux
    assert old_mac == linux
    assert chunk_policy_text("policy-v1", "第一段。\n\n第二段。") == linux


def test_chunk_policy_text_ignores_empty_paragraphs() -> None:
    chunks = chunk_policy_text(
        "policy-v1",
        "  \n\n第一段。\n\n\n\n  第二段。  \n\n",
    )

    assert [chunk.content for chunk in chunks] == ["第一段。", "第二段。"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]


def test_chunk_policy_text_preserves_page_provenance_in_chunk_and_id() -> None:
    page_one = chunk_policy_text(
        "refund-policy-v1",
        "相同政策内容。",
        page_number=1,
    )
    page_two = chunk_policy_text(
        "refund-policy-v1",
        "相同政策内容。",
        page_number=2,
    )

    assert page_one[0].page_number == 1
    assert page_two[0].page_number == 2
    assert page_one[0].chunk_id == expected_page_chunk_id(
        "refund-policy-v1",
        1,
        0,
        "相同政策内容。",
    )
    assert page_two[0].chunk_id == expected_page_chunk_id(
        "refund-policy-v1",
        2,
        0,
        "相同政策内容。",
    )
    assert page_one[0].chunk_id != page_two[0].chunk_id


@pytest.mark.parametrize(
    ("text_length", "expected_lengths"),
    [
        (800, [800]),
        (801, [800, 1]),
        (1700, [800, 800, 100]),
    ],
)
def test_chunk_policy_text_bounds_long_paragraphs(
    text_length: int,
    expected_lengths: list[int],
) -> None:
    chunks = chunk_policy_text(
        "long-policy-v1",
        "知" * text_length,
        page_number=7,
    )

    assert [len(chunk.content) for chunk in chunks] == expected_lengths
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.page_number == 7 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == "知" * text_length
    assert all(len(chunk.content) <= DEFAULT_MAX_CHARS for chunk in chunks)


@pytest.mark.parametrize("max_chars", [0, -1])
def test_chunk_policy_text_rejects_non_positive_max_chars(max_chars: int) -> None:
    with pytest.raises(ValueError, match="max_chars must be greater than zero"):
        chunk_policy_text("policy-v1", "政策内容。", max_chars=max_chars)


@pytest.mark.parametrize("page_number", [0, -1])
def test_chunk_policy_text_rejects_non_positive_page_number(page_number: int) -> None:
    with pytest.raises(ValueError, match="page_number must be greater than zero"):
        chunk_policy_text("policy-v1", "政策内容。", page_number=page_number)


@pytest.mark.parametrize("text", ["", "   ", "\r\n\r\n"])
def test_chunk_policy_text_returns_empty_list_for_empty_text(text: str) -> None:
    assert chunk_policy_text("policy-v1", text) == []


@pytest.mark.parametrize("source_id", ["", "   ", "\n\t"])
def test_chunk_policy_text_rejects_blank_source_id(source_id: str) -> None:
    with pytest.raises(ValueError, match="source_id must not be empty or whitespace"):
        chunk_policy_text(source_id, "政策内容。")


@pytest.mark.parametrize(
    ("returned_id", "expected_stored"),
    [("a" * 64, True), (None, False)],
)
async def test_try_store_knowledge_chunk_uses_idempotent_primary_key_insert(
    returned_id: str | None,
    expected_stored: bool,
) -> None:
    fake_engine = FakeStoreEngine(returned_id)
    chunk = KnowledgeChunk(
        chunk_id="a" * 64,
        source_id="refund-policy-v1",
        page_number=2,
        chunk_index=0,
        content="退款申请需由订单本人提交。",
    )

    stored = await try_store_knowledge_chunk(
        cast(AsyncEngine, fake_engine),
        chunk,
    )

    assert stored is expected_stored
    assert fake_engine.transaction.exited is True
    assert fake_engine.connection.execution is not None
    statement, parameters = fake_engine.connection.execution
    sql = str(statement)
    assert "INSERT INTO agent_core.knowledge_chunks" in sql
    assert "ON CONFLICT (chunk_id) DO NOTHING" in sql
    assert "RETURNING chunk_id" in sql
    assert parameters == {
        "chunk_id": "a" * 64,
        "source_id": "refund-policy-v1",
        "page_number": 2,
        "chunk_index": 0,
        "content": "退款申请需由订单本人提交。",
    }


async def test_store_knowledge_chunks_counts_inserts_in_one_transaction() -> None:
    chunks = chunk_policy_text(
        "refund-policy-v1",
        "第一段。\n\n第二段。\n\n第三段。",
    )
    fake_engine = FakeBatchEngine([chunks[0].chunk_id, None, chunks[2].chunk_id])

    stored_count = await store_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        chunks,
    )

    assert stored_count == 2
    assert fake_engine.begin_calls == 1
    assert fake_engine.transaction.exited is True
    assert fake_engine.transaction.exit_exception_type is None
    assert len(fake_engine.connection.executions) == 3
    assert [
        parameters["chunk_id"]
        for _, parameters in fake_engine.connection.executions
    ] == [chunk.chunk_id for chunk in chunks]


async def test_store_knowledge_chunks_does_not_open_transaction_for_empty_list() -> None:
    fake_engine = FakeBatchEngine([])

    stored_count = await store_knowledge_chunks(
        cast(AsyncEngine, fake_engine),
        [],
    )

    assert stored_count == 0
    assert fake_engine.begin_calls == 0


async def test_store_knowledge_chunks_exits_transaction_on_mid_batch_failure() -> None:
    chunks = chunk_policy_text(
        "refund-policy-v1",
        "第一段。\n\n第二段。\n\n第三段。",
    )
    fake_engine = FakeBatchEngine(
        [chunks[0].chunk_id, RuntimeError("simulated database failure")]
    )

    with pytest.raises(RuntimeError, match="simulated database failure"):
        await store_knowledge_chunks(
            cast(AsyncEngine, fake_engine),
            chunks,
        )

    assert fake_engine.begin_calls == 1
    assert fake_engine.transaction.exited is True
    assert fake_engine.transaction.exit_exception_type is RuntimeError
    assert len(fake_engine.connection.executions) == 2
