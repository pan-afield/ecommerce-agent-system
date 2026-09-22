import {
  REFUND_ERROR_CODES,
  REFUND_EXECUTION_STATUSES,
  REFUND_RECOVERY_STATUSES,
  REFUND_REASON_CODES,
  REFUND_STATUSES,
  type RefundApplication,
  type RefundAssessment,
  type RefundError,
  type RefundErrorCode,
  type RefundExecution,
  type RefundOperationDetail,
  type RefundOperationQueue,
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

export function isNullableRefundApplication(
  value: unknown,
): value is RefundApplication | null {
  return value === null || isRefundApplication(value);
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

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isIsoDateString(value: unknown): value is string {
  return isNonEmptyString(value) && !Number.isNaN(Date.parse(value));
}

export function isRefundExecution(value: unknown): value is RefundExecution {
  return (
    isRecord(value) &&
    isNonEmptyString(value.id) &&
    typeof value.status === "string" &&
    (REFUND_EXECUTION_STATUSES as readonly string[]).includes(value.status) &&
    isNonEmptyString(value.amount) &&
    isNonEmptyString(value.currency) &&
    isNullableString(value.provider_reference)
  );
}

export function isNullableRefundExecution(
  value: unknown,
): value is RefundExecution | null {
  return value === null || isRefundExecution(value);
}

export function isRefundOperationQueue(value: unknown): value is RefundOperationQueue {
  return (
    isRecord(value) &&
    Array.isArray(value.items) &&
    value.items.every(
      (item) =>
        isRecord(item) &&
        isNonEmptyString(item.refund_application_id) &&
        typeof item.execution_status === "string" &&
        (REFUND_EXECUTION_STATUSES as readonly string[]).includes(item.execution_status) &&
        typeof item.status === "string" &&
        (REFUND_RECOVERY_STATUSES as readonly string[]).includes(item.status) &&
        Number.isInteger(item.attempts) &&
        Number(item.attempts) >= 0 &&
        isNullableString(item.last_error_code) &&
        isIsoDateString(item.updated_at),
    )
  );
}

export function isRefundOperationDetail(value: unknown): value is RefundOperationDetail {
  return (
    isRecord(value) &&
    isRefundExecution(value.execution) &&
    (value.recovery === null ||
      (isRecord(value.recovery) &&
        typeof value.recovery.status === "string" &&
        (REFUND_RECOVERY_STATUSES as readonly string[]).includes(value.recovery.status) &&
        Number.isInteger(value.recovery.attempts) &&
        Number(value.recovery.attempts) >= 0 &&
        isIsoDateString(value.recovery.next_attempt_at) &&
        isNullableString(value.recovery.last_error_code) &&
        isIsoDateString(value.recovery.updated_at))) &&
    Array.isArray(value.events) &&
    value.events.every(
      (event) =>
        isRecord(event) &&
        isNonEmptyString(event.id) &&
        isNonEmptyString(event.action) &&
        isNonEmptyString(event.source) &&
        isNullableString(event.actor_user_id) &&
        isNullableString(event.source_event_id) &&
        isNullableString(event.from_status) &&
        isNullableString(event.to_status) &&
        isNullableString(event.provider_reference) &&
        isNullableString(event.error_code) &&
        isNullableString(event.note) &&
        isIsoDateString(event.created_at),
    ) &&
    typeof value.next_after_id === "string" &&
    /^\d+$/.test(value.next_after_id)
  );
}
