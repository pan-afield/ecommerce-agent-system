CREATE TABLE "refund_webhook_events" (
    "event_id" VARCHAR(128) PRIMARY KEY,
    "idempotency_key" VARCHAR(128) NOT NULL,
    "status" VARCHAR(32) NOT NULL,
    "provider_reference" VARCHAR(128),
    "received_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX "refund_webhook_events_idempotency_key_received_at_idx"
    ON "refund_webhook_events" ("idempotency_key", "received_at");