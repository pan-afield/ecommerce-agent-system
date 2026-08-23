-- CreateEnum
CREATE TYPE "RefundStatus" AS ENUM ('AWAITING_CUSTOMER_CONFIRMATION', 'PENDING_MANUAL_APPROVAL');

-- CreateTable
CREATE TABLE "refund_applications" (
    "id" VARCHAR(64) NOT NULL,
    "user_id" VARCHAR(64) NOT NULL,
    "order_id" VARCHAR(64) NOT NULL,
    "request_id" VARCHAR(128) NOT NULL,
    "requested_amount" DECIMAL(12,2) NOT NULL,
    "currency" CHAR(3) NOT NULL,
    "status" "RefundStatus" NOT NULL,
    "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "confirmed_at" TIMESTAMPTZ(3),

    CONSTRAINT "refund_applications_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "refund_applications_order_id_idx" ON "refund_applications"("order_id");

-- CreateIndex
CREATE UNIQUE INDEX "refund_applications_user_id_request_id_key" ON "refund_applications"("user_id", "request_id");

-- AddForeignKey
ALTER TABLE "refund_applications" ADD CONSTRAINT "refund_applications_user_id_fkey" FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "refund_applications" ADD CONSTRAINT "refund_applications_order_id_fkey" FOREIGN KEY ("order_id") REFERENCES "orders"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
