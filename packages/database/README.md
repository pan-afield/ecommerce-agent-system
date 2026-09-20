# Database Package

This package is the Prisma boundary for conventional application data used by the web
application: customers, orders, support tickets, and related audit records.

V0.2 introduces `User`, `Order`, and `ShipmentEvent`, their incremental migrations, and repeatable
Chinese demo seed data. Run migrations and seeds intentionally against a configured PostgreSQL
database; schema validation and client generation do not require a running database.

This package uses Prisma 7. The CLI connection URL and seed command live in `prisma.config.ts`;
`schema.prisma` declares the database provider and relational models only. The config loads the
repository root `.env` first and allows `packages/database/.env` to override it for package-local
development. Prisma Client runtime code must use the PostgreSQL driver adapter rather than creating
`PrismaClient` without an adapter.

Prisma does not own vector indexes, retrieval chunks, agent checkpoints, or other Python runtime
state. Those structures belong to versioned, backend-specific migrations under
`apps/agent-core/migrations` when a later phase requires them. Both owners may share PostgreSQL,
but a table must have exactly one migration owner.

## Local role accounts

The repeatable seed also creates three local-only accounts for V0.8 visibility
acceptance. The seed stores Argon2id password hashes; these credentials must not
be reused outside local development.

| Role | Email | Password | Knowledge visibility |
| --- | --- | --- | --- |
| CUSTOMER | `customer.demo@example.com` | `CustomerDemo123!` | `PUBLIC` |
| SUPPORT | `support.demo@example.com` | `SupportDemo123!` | `PUBLIC`, `SUPPORT` |
| ADMIN | `admin.demo@example.com` | `AdminDemo123!` | `PUBLIC`, `SUPPORT`, `ADMIN` |

## V1.0 refund audit, recovery and HTTP sandbox

Prisma owns `refund_executions`, `refund_webhook_events`, `refund_audit_events`,
`refund_recovery_jobs`, `sandbox_refunds`, and `sandbox_refund_events` in the application schema.
The final two tables belong to the local simulated payment provider; they contain no real payments.
SQLAlchemy reads/writes these tables but never creates them.

The `20260919090000_refund_audit_and_recovery` migration includes custom PostgreSQL triggers:
execution transitions, append-only audit and recovery scheduling commit atomically. UPDATE,
DELETE and TRUNCATE of audit history are rejected. Prisma's model does not express these triggers
or all CHECK constraints: retain the checked-in SQL and use `prisma migrate deploy`, not `db push`,
to install them. Existing executions receive a `BASELINE` snapshot, not invented historical events.

The `20260919100000_http_refund_sandbox` migration persists provider idempotency and stable callback
event IDs. Model, audit and compensation changes use the same migration directory for development
and test databases; select the target by connection configuration. Do not create a second set of
test-only migrations. Automated refund tests apply these SQL files in unique temporary schemas
inside `ecommerce_agents_test` and clean them up afterward.

See [the V1.0 sandbox runbook](../../docs/refund-sandbox-runbook.md) for configuration, manual
migration/start commands, API examples and recovery procedures. No development migration is
automatically applied by the backend lifecycle.
