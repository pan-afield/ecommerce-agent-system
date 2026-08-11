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
