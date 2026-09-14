import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.redis import consume_rate_limit


class FakeRateLimitPipeline:
    def __init__(self, redis: "FakeRateLimitRedis") -> None:
        self._redis = redis
        self._operations: list[tuple[str, str, int, bool]] = []

    async def __aenter__(self) -> "FakeRateLimitPipeline":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self._operations.append(("incr", key, 0, False))

    def expire(self, key: str, seconds: int, *, nx: bool) -> None:
        self._operations.append(("expire", key, seconds, nx))

    async def execute(self) -> list[int | bool]:
        if self._redis.error is not None:
            raise self._redis.error

        async with self._redis.lock:
            results: list[int | bool] = []
            for operation, key, seconds, nx in self._operations:
                self._redis._expire_keys()
                if operation == "incr":
                    value = self._redis.values.get(key, 0) + 1
                    self._redis.values[key] = value
                    results.append(value)
                else:
                    has_expiry = key in self._redis.expiry_at
                    if nx and has_expiry:
                        results.append(False)
                        continue
                    self._redis.expiry_at[key] = self._redis.now + seconds
                    results.append(True)

            return results


class FakeRateLimitRedis:
    def __init__(self, error: RedisError | None = None) -> None:
        self.values: dict[str, int] = {}
        self.expiry_at: dict[str, float] = {}
        self.now = 0.0
        self.error = error
        self.lock = asyncio.Lock()

    def pipeline(self, *, transaction: bool) -> FakeRateLimitPipeline:
        assert transaction is True
        return FakeRateLimitPipeline(self)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        self._expire_keys()

    def _expire_keys(self) -> None:
        expired = [key for key, deadline in self.expiry_at.items() if deadline <= self.now]
        for key in expired:
            self.expiry_at.pop(key, None)
            self.values.pop(key, None)


def as_redis(fake: FakeRateLimitRedis) -> Redis:
    return cast(Redis, fake)


async def allow_request(
    redis: FakeRateLimitRedis,
    key: str,
    *,
    limit: int = 2,
    window_seconds: int = 10,
) -> bool:
    return await consume_rate_limit(
        as_redis(redis),
        key,
        limit=limit,
        window_seconds=window_seconds,
    )


@pytest.mark.asyncio
async def test_consume_rate_limit_rejects_after_threshold() -> None:
    redis = FakeRateLimitRedis()

    assert await allow_request(redis, "rate:user-a") is True
    assert await allow_request(redis, "rate:user-a") is True
    assert await allow_request(redis, "rate:user-a") is False


@pytest.mark.asyncio
async def test_consume_rate_limit_allows_again_after_window_expires() -> None:
    redis = FakeRateLimitRedis()

    assert await allow_request(redis, "rate:user-a") is True
    assert await allow_request(redis, "rate:user-a") is True
    assert await allow_request(redis, "rate:user-a") is False

    redis.advance(10)

    assert await allow_request(redis, "rate:user-a") is True


@pytest.mark.asyncio
async def test_consume_rate_limit_isolates_different_users() -> None:
    redis = FakeRateLimitRedis()

    assert await allow_request(redis, "rate:user-a") is True
    assert await allow_request(redis, "rate:user-a") is True
    assert await allow_request(redis, "rate:user-a") is False
    assert await allow_request(redis, "rate:user-b") is True


@pytest.mark.asyncio
async def test_consume_rate_limit_fails_open_when_redis_is_unavailable() -> None:
    redis = FakeRateLimitRedis(error=RedisError("redis unavailable"))

    assert await allow_request(redis, "rate:user-a") is True


@pytest.mark.asyncio
async def test_consume_rate_limit_keeps_concurrent_requests_within_limit() -> None:
    redis = FakeRateLimitRedis()
    calls: list[Callable[[], Awaitable[bool]]] = [
        lambda: allow_request(redis, "rate:user-a") for _ in range(5)
    ]

    results = await asyncio.gather(*(call() for call in calls))

    assert results.count(True) == 2
    assert results.count(False) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit", "window_seconds"),
    [(0, 10), (-1, 10), (2, 0), (2, -1)],
)
async def test_consume_rate_limit_rejects_invalid_limits(
    limit: int,
    window_seconds: int,
) -> None:
    with pytest.raises(ValueError):
        await allow_request(
            FakeRateLimitRedis(),
            "rate:user-a",
            limit=limit,
            window_seconds=window_seconds,
        )
