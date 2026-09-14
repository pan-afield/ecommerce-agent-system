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
