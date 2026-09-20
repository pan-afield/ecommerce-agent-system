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
  "refund_rate_limited",
  "refund_not_found",
  "refund_conflict",
  "refund_ineligible",
  "refund_auth_unavailable",
  "refund_approver_unavailable",
  "refund_service_unavailable",
  "refund_upstream_unreachable",
  "refund_upstream_timeout",
  "refund_invalid_upstream_response",
  "refund_outcome_unknown",
  "refund_network_error",
  "refund_invalid_response",
] as const;

export const REFUND_EXECUTION_STATUSES = [
  "PENDING",
  "RUNNING",
  "PROCESSING",
  "SUCCEEDED",
  "FAILED",
] as const;

export const REFUND_RECOVERY_STATUSES = [
  "READY",
  "LEASED",
  "COMPLETED",
  "MANUAL_REQUIRED",
] as const;

export type RefundStatus = (typeof REFUND_STATUSES)[number];
export type RefundReasonCode = (typeof REFUND_REASON_CODES)[number];
export type RefundErrorCode = (typeof REFUND_ERROR_CODES)[number];
export type RefundReviewDecision = "APPROVED" | "REJECTED";
export type RefundExecutionStatus = (typeof REFUND_EXECUTION_STATUSES)[number];
export type RefundRecoveryStatus = (typeof REFUND_RECOVERY_STATUSES)[number];
export type RefundOperationAction = "resume" | "resubmit" | "acknowledge-conflict";

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

export interface RefundExecution {
  id: string;
  provider_reference: string | null;
  status: RefundExecutionStatus;
  amount: string;
  currency: string;
}

export interface RefundOperationQueueItem {
  refund_application_id: string;
  execution_status: RefundExecutionStatus;
  status: RefundRecoveryStatus;
  attempts: number;
  last_error_code: string | null;
  updated_at: string;
}

export interface RefundOperationQueue {
  items: RefundOperationQueueItem[];
}

export interface RefundRecoverySnapshot {
  status: RefundRecoveryStatus;
  attempts: number;
  next_attempt_at: string;
  last_error_code: string | null;
  updated_at: string;
}

export interface RefundAuditEvent {
  id: string;
  action: string;
  source: string;
  actor_user_id: string | null;
  source_event_id: string | null;
  from_status: string | null;
  to_status: string | null;
  provider_reference: string | null;
  error_code: string | null;
  note: string | null;
  created_at: string;
}

export interface RefundOperationDetail {
  execution: RefundExecution;
  recovery: RefundRecoverySnapshot | null;
  events: RefundAuditEvent[];
  next_after_id: string;
}
