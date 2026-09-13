import json
from typing import cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis | None:
    if settings.redis_url is None:
        return None

    return cast(
        Redis,
        Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        ),
    )


def build_rag_cache_key(
    query: str,
    *,
    embedding_model: str,
    embedding_dimensions: int,
    limit: int,
) -> str:
    normalized_query = query.strip()

    return f"rag:v1:{embedding_model}:{embedding_dimensions}:limit={limit}:{normalized_query}"


def serialize_cache_value(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def set_cache_value(
    client: Redis,
    key: str,
    value: object,
    *,
    ttl_seconds: int,
) -> bool:
    payload = serialize_cache_value(value)

    try:
        await client.set(
            key,
            payload,
            ex=ttl_seconds,
        )
    except RedisError:
        return False

    return True


async def get_cache_value(
    client: Redis,
    key: str,
) -> object | None:
    try:
        payload = await client.get(key)
    except RedisError:
        return None

    if payload is None:
        return None

    return cast(object, json.loads(payload))


async def delete_cache_value(
    client: Redis,
    key: str,
) -> bool:
    try:
        deleted = await client.delete(key)
    except RedisError:
        return False

    return bool(deleted)


async def invalidate_rag_cache(client: Redis) -> int | None:
    deleted_count = 0

    try:
        async for key in client.scan_iter(
            match="rag:v1:*",
            count=100,
        ):
            deleted_count += int(await client.delete(key))
    except RedisError:
        return None

    return deleted_count
