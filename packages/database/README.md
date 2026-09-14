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
