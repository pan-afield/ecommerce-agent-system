import {
  REFUND_ERROR_CODES,
  REFUND_REASON_CODES,
  REFUND_STATUSES,
  type RefundApplication,
  type RefundAssessment,
  type RefundError,
  type RefundErrorCode,
} from "@/types/refund";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

export function isRefundAssessment(value: unknown): value is RefundAssessment {
  return (
    isRecord(value) &&
    typeof value.eligible_for_review === "boolean" &&
    typeof value.reason === "string" &&
    (REFUND_REASON_CODES as readonly string[]).includes(value.reason) &&
    typeof value.requires_customer_confirmation === "boolean"
  );
}

export function isRefundApplication(value: unknown): value is RefundApplication {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    isNonEmptyString(value.order_id) &&
    isNonEmptyString(value.request_id) &&
    isNonEmptyString(value.requested_amount) &&
    isNonEmptyString(value.currency) &&
    typeof value.status === "string" &&
    (REFUND_STATUSES as readonly string[]).includes(value.status) &&
    (value.created === undefined || typeof value.created === "boolean") &&
    (value.reviewed_by_user_id === undefined ||
      value.reviewed_by_user_id === null ||
      isNonEmptyString(value.reviewed_by_user_id)) &&
    (value.reviewed_at === undefined ||
      value.reviewed_at === null ||
      isNonEmptyString(value.reviewed_at)) &&
    (value.review_note === undefined ||
      value.review_note === null ||
      typeof value.review_note === "string")
  );
}

export function isRefundErrorCode(value: unknown): value is RefundErrorCode {
  return (
    typeof value === "string" &&
    (REFUND_ERROR_CODES as readonly string[]).includes(value)
  );
}

export function isRefundError(value: unknown): value is RefundError {
  return (
    isRecord(value) &&
    isRecord(value.error) &&
    isRefundErrorCode(value.error.code) &&
    isNonEmptyString(value.error.message)
  );
}
