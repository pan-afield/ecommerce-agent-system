CREATE TABLE "refund_executions" (
    "id" VARCHAR(64) PRIMARY KEY,
    "refund_application_id" VARCHAR(64) NOT NULL UNIQUE,
    "idempotency_key" VARCHAR(128) NOT NULL UNIQUE,
    "status" VARCHAR(32) NOT NULL,
    "provider_reference" VARCHAR(128),
    "amount" DECIMAL(12, 2) NOT NULL,
    "currency" CHAR(3) NOT NULL,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);