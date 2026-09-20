-- 仅供本地 HTTP 支付沙箱使用，独立于客服系统的执行/审计事实表。
CREATE TABLE "sandbox_refunds" (
    "idempotency_key" VARCHAR(128) PRIMARY KEY,
    "payload_hash" CHAR(64) NOT NULL,
    "status" VARCHAR(32) NOT NULL DEFAULT 'PROCESSING' CHECK ("status" IN ('PROCESSING','SUCCEEDED','FAILED')),
    "provider_reference" VARCHAR(128),
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE "sandbox_refund_events" (
    "event_id" VARCHAR(128) PRIMARY KEY,
    "idempotency_key" VARCHAR(128) NOT NULL UNIQUE REFERENCES "sandbox_refunds"("idempotency_key"),
    "status" VARCHAR(32) NOT NULL CHECK ("status" IN ('SUCCEEDED','FAILED')),
    "provider_reference" VARCHAR(128),
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);
