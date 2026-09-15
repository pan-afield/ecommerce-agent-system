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
    visible_visibilities: tuple[str, ...] = ("PUBLIC",),
) -> str:
    normalized_query = query.strip()
    visibility_scope = ",".join(sorted(set(visible_visibilities)))

    return (
        f"rag:v1:{embedding_model}:{embedding_dimensions}:"
        f"visibility={visibility_scope}:limit={limit}:{normalized_query}"
    )


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


# ：同一个 key，在一个窗口内最多放行 limit 次请求。
async def consume_rate_limit(
    client: Redis,
    key: str,
    *,  # 后面的参数必须通过名称传入，例如 limit=30
    limit: int,
    window_seconds: int,
) -> bool:
    if limit <= 0:
        raise ValueError("limit must be greater than zero")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be greater than zero")

    try:
        # async with：退出时自动清理 pipeline，并归还占用的连接。
        async with client.pipeline(transaction=True) as pipeline:
            # INCR：把 key 对应的整数加 1；key 不存在时从 0 开始加。
            # 例如同一用户的计数：29 -> 30。
            # 这里仅将命令加入队列，因此不需要 await。
            pipeline.incr(key)
            # EXPIRE：给 key 设置过期时间，单位为秒。
            # nx=True：只有 key 当前没有过期时间时才设置。
            # 因此第一次请求开启窗口，后续请求不会延长窗口。
            # 窗口到期后 key 失效，下次 INCR 会重新从 1 开始。
            pipeline.expire(key, window_seconds, nx=True)
            # 真正提交队列中的命令，等待 Redis 返回执行结果。
            # results 与命令顺序一致：
            # results[0] 是 INCR 后的计数；
            # results[1] 是 EXPIRE 是否成功设置过期时间。
            results = await pipeline.execute()
    except RedisError:
        return True

    # 第 limit 次仍然放行，第 limit + 1 次开始拒绝。
    # 被拒绝的请求也已经计数，但不会延长窗口。
    return int(results[0]) <= limit
