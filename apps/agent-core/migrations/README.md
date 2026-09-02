# Agent Core Migrations

This directory contains versioned schema migrations owned by the Python Agent Core. They define
the `agent_core` PostgreSQL schema, including knowledge chunks and durable agent runtime state such
as ingestion tasks.

Traditional application tables belong to `packages/database/prisma` and must not be duplicated
here. Those Prisma migrations own records such as users, orders, shipments, and refunds.

These files are not test-only migrations. Apply the same ordered migrations to each environment's
separate database so development, test, staging, and production share one schema contract. For
example, automated integration tests use `ecommerce_agents_test`, while local development uses
`ecommerce_agents`.

Test rows and fixtures do not belong in this directory. Tests should create uniquely named rows in
the isolated test database and remove them after verification.
