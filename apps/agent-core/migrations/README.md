# Agent Core Migrations

Reserved for backend-owned PostgreSQL structures such as future pgvector indexes, retrieval
chunks, and agent runtime state. Phase 1 has no backend migrations.

Traditional application tables belong to `packages/database/prisma` and must not be duplicated
here.
