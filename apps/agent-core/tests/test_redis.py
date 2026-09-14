from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.redis import (
    build_rag_cache_key,
    create_redis_client,
    delete_cache_value,
    get_cache_value,
    invalidate_rag_cache,
    serialize_cache_value,
    set_cache_value,
)


def test_create_redis_client_returns_none_when_unconfigured() -> None:
    settings = Settings(_env_file=None)

    assert create_redis_client(settings) is None


@pytest.mark.asyncio
async def test_create_redis_client_uses_configured_url_without_connecting() -> None:
    settings = Settings(
        _env_file=None,
        redis_url="redis://localhost:6380/2",
    )

    client = create_redis_client(settings)

    assert client is not None
    assert client.connection_pool.connection_kwargs["host"] == "localhost"
    assert client.connection_pool.connection_kwargs["port"] == 6380
    assert client.connection_pool.connection_kwargs["db"] == 2
    assert client.connection_pool.connection_kwargs["decode_responses"] is True

    await client.aclose()


def test_build_rag_cache_key_normalizes_query_and_contains_embedding_identity() -> None:
    key = build_rag_cache_key(
        "  退款政策  ",
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=1024,
        limit=3,
    )

    assert key == "rag:v1:BAAI/bge-m3:1024:visibility=PUBLIC:limit=3:退款政策"


def test_build_rag_cache_key_separates_embedding_model_and_dimensions() -> None:
    base = build_rag_cache_key(
        "退款政策",
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=1024,
        limit=3,
    )
    different_model = build_rag_cache_key(
        "退款政策",
        embedding_model="another-model",
        embedding_dimensions=1024,
        limit=3,
    )
    different_dimensions = build_rag_cache_key(
        "退款政策",
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=768,
        limit=3,
    )

    assert len({base, different_model, different_dimensions}) == 3


def test_build_rag_cache_key_separates_result_limit() -> None:
    first = build_rag_cache_key(
        "退款政策",
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=1024,
        limit=1,
    )
    second = build_rag_cache_key(
        "退款政策",
        embedding_model="BAAI/bge-m3",
        embedding_dimensions=1024,
        limit=3,
    )

    assert first != second


def test_serialize_cache_value_keeps_chinese_and_uses_compact_json() -> None:
    serialized = serialize_cache_value(
        {
            "query": "退款政策",
            "citations": [
                {"source_id": "refund-policy.md", "score": 0.91},
            ],
        }
    )

    assert serialized == (
        '{"query":"退款政策","citations":[{"source_id":"refund-policy.md",'
        '"score":0.91}]}'
    )
    assert "\\u" not in serialized


@pytest.mark.asyncio
async def test_set_cache_value_serializes_value_and_passes_ttl() -> None:
    client = MagicMock()
    client.set = AsyncMock()
    value = {"query": "退款政策", "citations": []}

    written = await set_cache_value(
        client,
        "rag:v1:BAAI/bge-m3:1024:limit=3:退款政策",
        value,
        ttl_seconds=60,
    )

    assert written is True
    client.set.assert_awaited_once_with(
        "rag:v1:BAAI/bge-m3:1024:limit=3:退款政策",
        '{"query":"退款政策","citations":[]}',
        ex=60,
    )


@pytest.mark.asyncio
async def test_set_cache_value_returns_false_when_redis_fails() -> None:
    client = MagicMock()
    client.set = AsyncMock(side_effect=RedisError("redis unavailable"))

    written = await set_cache_value(
        client,
        "rag:key",
        {"citations": []},
        ttl_seconds=60,
    )

    assert written is False
    client.set.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_cache_value_deserializes_a_hit() -> None:
    client = MagicMock()
    client.get = AsyncMock(
        return_value='{"query":"退款政策","citations":[]}',
    )

    value = await get_cache_value(client, "rag:key")

    assert value == {"query": "退款政策", "citations": []}
    client.get.assert_awaited_once_with("rag:key")


@pytest.mark.asyncio
async def test_get_cache_value_returns_none_on_a_miss() -> None:
    client = MagicMock()
    client.get = AsyncMock(return_value=None)

    value = await get_cache_value(client, "rag:key")

    assert value is None
    client.get.assert_awaited_once_with("rag:key")


@pytest.mark.asyncio
async def test_get_cache_value_treats_redis_failure_as_a_cache_miss() -> None:
    client = MagicMock()
    client.get = AsyncMock(side_effect=RedisError("redis unavailable"))

    value = await get_cache_value(client, "rag:key")

    assert value is None
    client.get.assert_awaited_once_with("rag:key")


@pytest.mark.asyncio
@pytest.mark.parametrize("deleted_count, expected", [(1, True), (0, False)])
async def test_delete_cache_value_returns_whether_key_was_deleted(
    deleted_count: int,
    expected: bool,
) -> None:
    client = MagicMock()
    client.delete = AsyncMock(return_value=deleted_count)

    deleted = await delete_cache_value(client, "rag:key")

    assert deleted is expected
    client.delete.assert_awaited_once_with("rag:key")


@pytest.mark.asyncio
async def test_delete_cache_value_returns_false_when_redis_fails() -> None:
    client = MagicMock()
    client.delete = AsyncMock(side_effect=RedisError("redis unavailable"))

    deleted = await delete_cache_value(client, "rag:key")

    assert deleted is False
    client.delete.assert_awaited_once_with("rag:key")


@pytest.mark.asyncio
async def test_invalidate_rag_cache_scans_namespace_and_deletes_matching_keys() -> None:
    client = MagicMock()

    async def keys() -> AsyncIterator[str]:
        for key in ("rag:v1:key-a", "rag:v1:key-b"):
            yield key

    client.scan_iter.return_value = keys()
    client.delete = AsyncMock(side_effect=[1, 0])

    deleted = await invalidate_rag_cache(client)

    assert deleted == 1
    client.scan_iter.assert_called_once_with(match="rag:v1:*", count=100)
    assert client.delete.await_args_list[0].args == ("rag:v1:key-a",)
    assert client.delete.await_args_list[1].args == ("rag:v1:key-b",)


@pytest.mark.asyncio
async def test_invalidate_rag_cache_returns_none_when_redis_fails() -> None:
    client = MagicMock()

    async def keys() -> AsyncIterator[str]:
        yield "rag:v1:key-a"
        raise RedisError("redis unavailable")

    client.scan_iter.return_value = keys()
    client.delete = AsyncMock(return_value=1)

    deleted = await invalidate_rag_cache(client)

    assert deleted is None
    client.delete.assert_awaited_once_with("rag:v1:key-a")
