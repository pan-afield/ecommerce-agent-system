-- CreateTable
CREATE TABLE "public"."shipment_events" (
    "id" VARCHAR(64) NOT NULL,
    "order_id" VARCHAR(64) NOT NULL,
    "status" VARCHAR(50) NOT NULL,
    "description" VARCHAR(255) NOT NULL,
    "location" VARCHAR(100),
    "occurred_at" TIMESTAMPTZ(3) NOT NULL,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "shipment_events_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "shipment_events_order_id_occurred_at_idx" ON "public"."shipment_events"("order_id", "occurred_at");

-- AddForeignKey
ALTER TABLE "public"."shipment_events" ADD CONSTRAINT "shipment_events_order_id_fkey" FOREIGN KEY ("order_id") REFERENCES "public"."orders"("id") ON DELETE CASCADE ON UPDATE CASCADE;
