# Agent Core

Python 3.12 service boundary for the ecommerce customer-service system.

## Phase 1 scope

- FastAPI application factory and lifespan management
- Environment-backed settings
- Async SQLAlchemy engine ownership
- Liveness and database-backed readiness endpoints

The `agents`, `tools`, and `rag` packages are ownership placeholders only. They intentionally
contain no orchestration, retrieval, refund, or model-provider behavior in this phase.

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
