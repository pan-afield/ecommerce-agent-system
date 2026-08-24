import { isRefundApplication } from "@/lib/refund-contract";
import type { RefundApplication } from "@/types/refund";

export const REFUND_SESSION_STORAGE_KEY = "relay-desk-refund-session-v0.5";
export const REFUND_REQUEST_STORAGE_KEY = "relay-desk-refund-request-v0.5";

export interface RefundRequestDraft {
  amount: string;
  currency: string;
  orderId: string;
  requestId: string;
}

export function loadRefundSession(storage: Storage): RefundApplication | null {
  try {
    const rawValue = storage.getItem(REFUND_SESSION_STORAGE_KEY);
    if (rawValue === null) {
      return null;
    }
    const value: unknown = JSON.parse(rawValue);
    return isRefundApplication(value) ? value : null;
  } catch {
    return null;
  }
}

export function saveRefundSession(storage: Storage, application: RefundApplication) {
  try {
    storage.setItem(REFUND_SESSION_STORAGE_KEY, JSON.stringify(application));
  } catch {
    // Refund actions still work when session storage is blocked.
  }
}

export function clearRefundSession(storage: Storage) {
  try {
    storage.removeItem(REFUND_SESSION_STORAGE_KEY);
  } catch {
    // A blocked storage API must not block a new in-memory refund flow.
  }
}

export function loadRefundRequest(storage: Storage): RefundRequestDraft | null {
  try {
    const rawValue = storage.getItem(REFUND_REQUEST_STORAGE_KEY);
    if (rawValue === null) {
      return null;
    }
    const value: unknown = JSON.parse(rawValue);
    if (
      typeof value !== "object" ||
      value === null ||
      !("amount" in value) ||
      !("currency" in value) ||
      !("orderId" in value) ||
      !("requestId" in value) ||
      typeof value.amount !== "string" ||
      typeof value.currency !== "string" ||
      typeof value.orderId !== "string" ||
      typeof value.requestId !== "string"
    ) {
      return null;
    }
    return value as RefundRequestDraft;
  } catch {
    return null;
  }
}

export function saveRefundRequest(storage: Storage, request: RefundRequestDraft) {
  try {
    storage.setItem(REFUND_REQUEST_STORAGE_KEY, JSON.stringify(request));
  } catch {
    // Idempotency remains available in memory when session storage is blocked.
  }
}

export function clearRefundRequest(storage: Storage) {
  try {
    storage.removeItem(REFUND_REQUEST_STORAGE_KEY);
  } catch {
    // A blocked storage API must not block a completed refund flow.
  }
}
