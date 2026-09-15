import asyncio
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
        # 模拟网络等待，让并发调用先重叠，再由 Fake 的锁模拟 Redis 顺序执行。
        await asyncio.sleep(0)
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
        self.pipeline_calls = 0
        self.cached_values: dict[str, str] = {}
        self.cache_operations: list[tuple[str, str]] = []

    def pipeline(self, *, transaction: bool) -> FakeRateLimitPipeline:
        assert transaction is True
        self.pipeline_calls += 1
        return FakeRateLimitPipeline(self)

    async def get(self, key: str) -> str | None:
        self.cache_operations.append(("get", key))
        if self.error is not None:
            raise self.error
        return self.cached_values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> bool:
        self.cache_operations.append(("set", key))
        if self.error is not None:
            raise self.error
        assert ex > 0
        # 仅模拟路由所需的缓存读写；限流计数的时间由 advance() 控制。
        self.cached_values[key] = value
        return True

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
async def test_later_and_rejected_requests_do_not_extend_rate_limit_window() -> None:
    redis = FakeRateLimitRedis()
    assert await allow_request(redis, "rate:user-a") is True
    redis.advance(4)
    assert await allow_request(redis, "rate:user-a") is True
    redis.advance(5.5)
    assert await allow_request(redis, "rate:user-a") is False
    assert redis.expiry_at["rate:user-a"] == 10
    redis.advance(0.5)
    assert await allow_request(redis, "rate:user-a") is True
    assert redis.values["rate:user-a"] == 1


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
    results = await asyncio.gather(
        *(allow_request(redis, "rate:user-a") for _ in range(5))
    )

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
    redis = FakeRateLimitRedis()
    with pytest.raises(ValueError):
        await allow_request(
            redis,
            "rate:user-a",
            limit=limit,
            window_seconds=window_seconds,
        )
    assert redis.pipeline_calls == 0
