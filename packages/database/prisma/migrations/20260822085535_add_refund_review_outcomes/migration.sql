-- AlterEnum
-- This migration adds more than one value to an enum.
-- With PostgreSQL versions 11 and earlier, this is not possible
-- in a single migration. This can be worked around by creating
-- multiple migrations, each migration adding only one value to
-- the enum.


ALTER TYPE "RefundStatus" ADD VALUE 'APPROVED';
ALTER TYPE "RefundStatus" ADD VALUE 'REJECTED';

-- AlterTable
ALTER TABLE "refund_applications" ADD COLUMN     "review_note" VARCHAR(500),
ADD COLUMN     "reviewed_at" TIMESTAMPTZ(3),
ADD COLUMN     "reviewed_by_user_id" VARCHAR(64);
