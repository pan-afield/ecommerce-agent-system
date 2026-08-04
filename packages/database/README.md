# Database Package

This package is the future Prisma boundary for conventional application data used by the web
application: customers, orders, support tickets, and related audit records.

Phase 1 intentionally defines no models or migrations. Add business models to
`prisma/schema.prisma` only when their feature is introduced, and commit each generated migration
with that feature.

Prisma does not own vector indexes, retrieval chunks, agent checkpoints, or other Python runtime
state. Those structures belong to versioned, backend-specific migrations under
`apps/agent-core/migrations` when a later phase requires them. Both owners may share PostgreSQL,
but a table must have exactly one migration owner.
