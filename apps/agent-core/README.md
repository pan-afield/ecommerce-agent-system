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
