# Agent Core

Python 3.12 service boundary for the ecommerce customer-service system.

## V0.1 scope

- FastAPI application factory and lifespan management
- Environment-backed settings
- Async SQLAlchemy engine ownership
- Liveness and database-backed readiness endpoints
- Async `POST /v1/chat` backed by `langchain-openai`
- Stable provider error responses and dependency-injected test doubles
- Read-only owned-order details backed by PostgreSQL
- Chronologically ordered shipment events and a temporary server-side demo identity

The `agents`, `tools`, and `rag` packages are ownership placeholders only. V0.1 intentionally
contains no LangGraph orchestration, tools, retrieval, order access, or refund behavior.

## Local checks

Create a Python 3.12 virtual environment, install `requirements-dev.txt`, then run:

```bash
python3.12 -m pytest
python3.12 -m ruff check app tests
python3.12 -m mypy app tests
```

The Turbo wrapper exposes the same checks as `test`, `lint`, and `typecheck`. Its `dev` script
is provided for workspace integration but must only be run intentionally.

## Health contract

- `GET /health/live` returns `200` when the HTTP process is responsive.
- `GET /health/ready` runs `SELECT 1` through the async SQLAlchemy engine. It returns `200` when
  PostgreSQL is reachable and `503` otherwise.

## Chat contract

- `POST /v1/chat` accepts `{"message": "..."}` and returns assistant content plus the configured
  model name.
- The message is trimmed, must not be empty, and is limited to 2,000 characters.
- Automated tests replace the model/service boundary and never require a real API key.
- See `docs/learning/v0.1-backend.md` for the request flow and learning notes.

## Order contract

- `GET /v1/orders/{order_id}` returns an owned order and its chronological shipment events.
- The temporary current user comes from server-side `DEMO_USER_ID`; client input cannot select a
  different owner.
- Missing and non-owned orders both return `404`; database failures return a sanitized `503`.
- See `docs/learning/v0.2-backend.md` for the evolutionary implementation and database boundary.

## Knowledge visibility acceptance

The RAG route filters public knowledge by the authenticated user's database role:

- `CUSTOMER`: `PUBLIC`
- `SUPPORT`: `PUBLIC` and `SUPPORT`
- `ADMIN`: `PUBLIC`, `SUPPORT`, and `ADMIN`

Local, non-sensitive fixtures are provided in `tests/fixtures/rag-visibility/`. From the
repository root, run the following commands after configuring `DATABASE_URL` and applying the
`knowledge_chunks.visibility` migrations:

```bash
cd apps/agent-core
.venv/bin/python -m app.rag.ingestion_cli \
  --visibility PUBLIC tests/fixtures/rag-visibility/public-policy.md
.venv/bin/python -m app.rag.ingestion_cli \
  --visibility SUPPORT tests/fixtures/rag-visibility/support-policy.md
.venv/bin/python -m app.rag.ingestion_cli \
  --visibility ADMIN tests/fixtures/rag-visibility/admin-policy.md
```

Run each command without `--rebuild`: rebuild replaces the whole knowledge table and would remove
the previously imported visibility scopes. Sign in with the role accounts documented in
`packages/database/README.md`, then query the same policy phrase. A customer should receive only
the public citation; support should receive public and support citations; admin should receive all
three. The API decides visibility from the database role, so clients must not send or invent a
visibility value.

## V0.9 per-user rate limiting (in progress)

The first V0.9 slice adds a fixed window counter to the authenticated chat and RAG routes.
Configure these values in the repository root `.env` or `apps/agent-core/.env`:

```dotenv
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
```

The Redis URL must point to an environment you manage; this example does not start Redis.
Use Redis 7+ for `EXPIRE NX`. Leaving `REDIS_URL` blank disables caching and rate limiting.

| Routes | Redis counter key | Local limit error |
| --- | --- | --- |
| `POST /v1/chat`, `POST /v1/chat/stream` | `rate:v1:chat:user:{JWT sub}` | `chat_rate_limited` |
| `GET /v1/rag/search` | `rate:v1:rag:user:{JWT sub}` | `rag_rate_limited` |

The default is 30 allowed requests per user per bucket within a window starting at the first
request. Later requests, including rejected attempts, do not extend the TTL. RAG cache hits also
consume quota; the RAG `limit` query parameter still controls citation count only. Identity comes
from a verified JWT, and existing database role checks continue to restrict RAG visibility.

Excess requests receive JSON `429` with `Retry-After` set conservatively to the full window length.
For SSE this happens before the stream starts. Redis errors fail open: the service remains
available, but the quota cannot be guaranteed. This counter does not replace PostgreSQL business
state or checkpoint idempotency. Replaying a `request_id` consumes an HTTP attempt while the
existing checkpoint can still reuse the completed answer.

See [V0.9 learning notes](../../docs/learning/v0.9-backend.md) for the request flow, verification
results, and remaining manual checks. Automated checks use Fake Redis and mocked services:

```bash
.venv/bin/python -m pytest tests/test_rate_limit.py tests/test_rate_limit_routes.py \
  tests/test_chat_route.py tests/test_rag_route.py tests/test_config.py tests/test_redis.py \
  tests/test_chat_service.py tests/test_support_graph.py -o addopts='' -q
```
