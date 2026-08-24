export const REFUND_REQUEST_ID_MAX_LENGTH = 128;
export const REFUND_REVIEW_NOTE_MAX_LENGTH = 500;

export const REFUND_STATUSES = [
  "AWAITING_CUSTOMER_CONFIRMATION",
  "PENDING_MANUAL_APPROVAL",
  "APPROVED",
  "REJECTED",
] as const;

export const REFUND_REASON_CODES = [
  "order_not_owned",
  "invalid_amount",
  "currency_mismatch",
  "amount_exceeds_order_total",
  "order_not_refundable",
  "eligible_for_review",
] as const;

export const REFUND_ERROR_CODES = [
  "refund_invalid_request",
  "refund_unauthorized",
  "refund_forbidden",
  "refund_not_found",
  "refund_conflict",
  "refund_ineligible",
  "refund_auth_unavailable",
  "refund_approver_unavailable",
  "refund_service_unavailable",
  "refund_upstream_unreachable",
  "refund_upstream_timeout",
  "refund_invalid_upstream_response",
  "refund_network_error",
  "refund_invalid_response",
] as const;

export type RefundStatus = (typeof REFUND_STATUSES)[number];
export type RefundReasonCode = (typeof REFUND_REASON_CODES)[number];
export type RefundErrorCode = (typeof REFUND_ERROR_CODES)[number];
export type RefundReviewDecision = "APPROVED" | "REJECTED";

export interface RefundAssessment {
  eligible_for_review: boolean;
  reason: RefundReasonCode;
  requires_customer_confirmation: boolean;
}

export interface RefundApplication {
  id: string;
  order_id: string;
  request_id: string;
  requested_amount: string;
  currency: string;
  status: RefundStatus;
  created?: boolean;
  reviewed_by_user_id?: string | null;
  reviewed_at?: string | null;
  review_note?: string | null;
}

export interface RefundError {
  error: {
    code: RefundErrorCode;
    message: string;
  };
}
