import { describe, expect, it } from "vitest";

import type { RefundApplication } from "@/types/refund";

import {
  clearRefundSession,
  clearRefundRequest,
  loadRefundRequest,
  loadRefundSession,
  REFUND_REQUEST_STORAGE_KEY,
  REFUND_SESSION_STORAGE_KEY,
  saveRefundRequest,
  saveRefundSession,
} from "./refund-session";

const application: RefundApplication = {
  id: "refund-001",
  order_id: "order-demo-001",
  request_id: "refund-request-001",
  requested_amount: "88.00",
  currency: "CNY",
  status: "PENDING_MANUAL_APPROVAL",
};

describe("refund session", () => {
  it("restores and clears the last authoritative application", () => {
    saveRefundSession(window.sessionStorage, application);
    expect(loadRefundSession(window.sessionStorage)).toEqual(application);

    clearRefundSession(window.sessionStorage);
    expect(loadRefundSession(window.sessionStorage)).toBeNull();
  });

  it("rejects malformed persisted state", () => {
    window.sessionStorage.setItem(
      REFUND_SESSION_STORAGE_KEY,
      JSON.stringify({ ...application, status: "MADE_UP" }),
    );

    expect(loadRefundSession(window.sessionStorage)).toBeNull();
  });

  it("preserves the idempotency key across an interrupted create request", () => {
    const request = {
      amount: "88.00",
      currency: "CNY",
      orderId: "order-demo-001",
      requestId: "refund-request-001",
    };
    saveRefundRequest(window.sessionStorage, request);
    expect(loadRefundRequest(window.sessionStorage)).toEqual(request);

    clearRefundRequest(window.sessionStorage);
    expect(window.sessionStorage.getItem(REFUND_REQUEST_STORAGE_KEY)).toBeNull();
  });
});
