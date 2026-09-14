from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

import app.rag.ingestion_cli as ingestion_cli
from app.core.config import Settings
from app.rag.chunking import KnowledgeChunk


class FakeEngine:
    def __init__(self) -> None:
        self.dispose_calls = 0

    async def dispose(self) -> None:
        self.dispose_calls += 1


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@localhost:5432/test",
        rag_embedding_model="test-local-model",
    )


def make_chunks() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            chunk_id="a" * 64,
            source_id="refund.md",
            chunk_index=0,
            content="退款政策。",
        )
    ]


@pytest.mark.parametrize("rebuild", [False, True])
@pytest.mark.asyncio
async def test_write_policy_chunks_uses_selected_operation_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
    rebuild: bool,
) -> None:
    settings = make_settings()
    chunks = make_chunks()
    embeddings = object()
    engine = FakeEngine()
    create_embeddings = Mock(return_value=embeddings)
    create_engine = Mock(return_value=engine)
    ingest = AsyncMock(return_value=1)
    rebuild_chunks = AsyncMock(return_value=1)
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock()
    invalidate = AsyncMock(return_value=1)
    monkeypatch.setattr(ingestion_cli, "create_local_embeddings", create_embeddings)
    monkeypatch.setattr(ingestion_cli, "create_database_engine", create_engine)
    monkeypatch.setattr(ingestion_cli, "ingest_knowledge_chunks", ingest)
    monkeypatch.setattr(ingestion_cli, "rebuild_knowledge_chunks", rebuild_chunks)
    monkeypatch.setattr(ingestion_cli, "create_redis_client", Mock(return_value=fake_redis))
    monkeypatch.setattr(ingestion_cli, "invalidate_rag_cache", invalidate)

    count = await ingestion_cli.write_policy_chunks(
        settings,
        chunks,
        rebuild=rebuild,
    )

    assert count == 1
    create_embeddings.assert_called_once_with(settings)
    create_engine.assert_called_once_with(settings.database_url)
    selected = rebuild_chunks if rebuild else ingest
    skipped = ingest if rebuild else rebuild_chunks
    selected.assert_awaited_once_with(
        engine,
        chunks,
        embeddings,
        embedding_model="test-local-model",
    )
    skipped.assert_not_awaited()
    invalidate.assert_awaited_once_with(fake_redis)
    fake_redis.aclose.assert_awaited_once()
    assert engine.dispose_calls == 1


@pytest.mark.asyncio
async def test_write_policy_chunks_disposes_engine_when_operation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    fake_redis = MagicMock()
    fake_redis.aclose = AsyncMock()
    invalidate = AsyncMock()
    monkeypatch.setattr(ingestion_cli, "create_local_embeddings", Mock(return_value=object()))
    monkeypatch.setattr(ingestion_cli, "create_database_engine", Mock(return_value=engine))
    monkeypatch.setattr(ingestion_cli, "create_redis_client", Mock(return_value=fake_redis))
    monkeypatch.setattr(ingestion_cli, "invalidate_rag_cache", invalidate)
    monkeypatch.setattr(
        ingestion_cli,
        "ingest_knowledge_chunks",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await ingestion_cli.write_policy_chunks(
            make_settings(),
            make_chunks(),
            rebuild=False,
        )

    assert engine.dispose_calls == 1
    invalidate.assert_not_awaited()
    fake_redis.aclose.assert_awaited_once()


@pytest.mark.parametrize(
    ("argv", "expected_rebuild"),
    [
        (["refund.md", "shipping.txt"], False),
        (["--rebuild", "refund.md", "shipping.txt"], True),
    ],
)
def test_argument_parser_accepts_multiple_files_and_rebuild_mode(
    argv: list[str],
    expected_rebuild: bool,
) -> None:
    arguments = ingestion_cli.build_argument_parser().parse_args(argv)

    assert arguments.file_paths == [Path("refund.md"), Path("shipping.txt")]
    assert arguments.rebuild is expected_rebuild
    assert arguments.visibility == "PUBLIC"


@pytest.mark.parametrize("visibility", ["PUBLIC", "SUPPORT", "ADMIN"])
def test_argument_parser_accepts_visibility_scope(visibility: str) -> None:
    arguments = ingestion_cli.build_argument_parser().parse_args(
        ["--visibility", visibility, "refund.md"]
    )

    assert arguments.visibility == visibility


def test_argument_parser_requires_at_least_one_file() -> None:
    with pytest.raises(SystemExit) as error:
        ingestion_cli.build_argument_parser().parse_args([])

    assert error.value.code == 2


@pytest.mark.parametrize("rebuild", [False, True])
def test_main_loads_files_runs_async_write_and_reports_count(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rebuild: bool,
) -> None:
    settings = make_settings()
    chunks = make_chunks()
    load_files = Mock(return_value=chunks)
    get_settings = Mock(return_value=settings)
    write = AsyncMock(return_value=2)
    monkeypatch.setattr(ingestion_cli, "load_policy_files_chunks", load_files)
    monkeypatch.setattr(ingestion_cli, "get_settings", get_settings)
    monkeypatch.setattr(ingestion_cli, "write_policy_chunks", write)
    argv = ["--rebuild", "refund.md"] if rebuild else ["refund.md"]

    exit_code = ingestion_cli.main(argv)

    assert exit_code == 0
    load_files.assert_called_once_with(
        [Path("refund.md")],
        visibility="PUBLIC",
    )
    get_settings.assert_called_once_with()
    write.assert_awaited_once_with(settings, chunks, rebuild=rebuild)
    assert capsys.readouterr().out == "已写入 2 个知识块。\n"
