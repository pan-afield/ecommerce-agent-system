-- A rejected application no longer occupies the order's refund slot.
-- All other statuses permit at most one application per user and order.
CREATE UNIQUE INDEX "refund_applications_user_id_order_id_non_rejected_key"
ON "refund_applications"("user_id", "order_id")
WHERE "status" <> 'REJECTED';
