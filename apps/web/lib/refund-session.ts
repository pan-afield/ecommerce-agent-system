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

function scopedKey(key: string, scope?: string) {
  return scope ? `${key}:${encodeURIComponent(scope)}` : key;
}

export function loadRefundSession(storage: Storage, scope?: string): RefundApplication | null {
  try {
    const rawValue = storage.getItem(scopedKey(REFUND_SESSION_STORAGE_KEY, scope));
    if (rawValue === null) {
      return null;
    }
    const value: unknown = JSON.parse(rawValue);
    return isRefundApplication(value) ? value : null;
  } catch {
    return null;
  }
}

export function saveRefundSession(storage: Storage, application: RefundApplication, scope?: string) {
  try {
    storage.setItem(scopedKey(REFUND_SESSION_STORAGE_KEY, scope), JSON.stringify(application));
  } catch {
    // Refund actions still work when session storage is blocked.
  }
}

export function clearRefundSession(storage: Storage, scope?: string) {
  try {
    storage.removeItem(scopedKey(REFUND_SESSION_STORAGE_KEY, scope));
  } catch {
    // A blocked storage API must not block a new in-memory refund flow.
  }
}

export function loadRefundRequest(storage: Storage, scope?: string): RefundRequestDraft | null {
  try {
    const rawValue = storage.getItem(scopedKey(REFUND_REQUEST_STORAGE_KEY, scope));
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

export function saveRefundRequest(storage: Storage, request: RefundRequestDraft, scope?: string) {
  try {
    storage.setItem(scopedKey(REFUND_REQUEST_STORAGE_KEY, scope), JSON.stringify(request));
  } catch {
    // Idempotency remains available in memory when session storage is blocked.
  }
}

export function clearRefundRequest(storage: Storage, scope?: string) {
  try {
    storage.removeItem(scopedKey(REFUND_REQUEST_STORAGE_KEY, scope));
  } catch {
    // A blocked storage API must not block a completed refund flow.
  }
}
