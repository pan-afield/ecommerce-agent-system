import { afterEach, describe, expect, it, vi } from "vitest";

import type { RefundApplication, RefundAssessment } from "@/types/refund";

import {
  assessRefund,
  confirmRefundApplication,
  createRefundApplication,
  getCurrentRefundApplication,
  reviewRefundApplication,
} from "./refund-api";

const assessment: RefundAssessment = {
  eligible_for_review: true,
  reason: "eligible_for_review",
  requires_customer_confirmation: true,
};

const application: RefundApplication = {
  id: "refund-001",
  order_id: "order-demo-001",
  request_id: "refund-request-001",
  requested_amount: "88.00",
  currency: "CNY",
  status: "AWAITING_CUSTOMER_CONFIRMATION",
  created: true,
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("refund API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the four same-origin refund actions", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(assessment))
      .mockResolvedValueOnce(jsonResponse(application))
      .mockResolvedValueOnce(
        jsonResponse({ ...application, status: "PENDING_MANUAL_APPROVAL", created: undefined }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          ...application,
          status: "APPROVED",
          created: undefined,
          reviewed_by_user_id: "staff-zhang",
          reviewed_at: "2026-08-23T10:00:00Z",
          review_note: "已核对",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(assessRefund("order-demo-001", "88.00", "CNY")).resolves.toEqual(assessment);
    await expect(
      createRefundApplication("order-demo-001", "refund-request-001", "88.00", "CNY"),
    ).resolves.toMatchObject({ id: "refund-001" });
    await expect(confirmRefundApplication("refund-001")).resolves.toMatchObject({
      status: "PENDING_MANUAL_APPROVAL",
    });
    await expect(
      reviewRefundApplication("refund-001", "APPROVED", "已核对"),
    ).resolves.toMatchObject({ status: "APPROVED" });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/orders/order-demo-001/refund-assessment",
      "/api/orders/order-demo-001/refund-applications",
      "/api/refund-applications/refund-001/confirm",
      "/api/refund-applications/refund-001/review",
    ]);
  });

  it("reads the current application and treats only the explicit missing-application 404 as empty", async () => {
    const currentApplication = { ...application };
    delete (currentApplication as Partial<typeof application>).created;
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(currentApplication))
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "refund_not_found", message: "退款申请不存在。" } },
          404,
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "refund_not_found", message: "订单不存在。" } },
          404,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(getCurrentRefundApplication(" order-demo-001 ")).resolves.toEqual(
      currentApplication,
    );
    await expect(getCurrentRefundApplication("order-demo-001")).resolves.toBeNull();
    await expect(getCurrentRefundApplication("order-demo-001")).rejects.toMatchObject({
      code: "refund_not_found",
      message: "订单不存在。",
      status: 404,
    });
    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/orders/order-demo-001/refund-application",
      expect.objectContaining({ method: "GET", body: undefined }),
    ]);
  });

  it("keeps GET 503 as a real error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          {
            error: {
              code: "refund_service_unavailable",
              message: "退款服务暂时不可用，请稍后重试。",
            },
          },
          503,
        ),
      ),
    );

    await expect(getCurrentRefundApplication("order-demo-001")).rejects.toMatchObject({
      code: "refund_service_unavailable",
      status: 503,
    });
  });

  it("preserves stable BFF errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "refund_conflict", message: "退款申请已被处理。" } },
          409,
        ),
      ),
    );

    await expect(confirmRefundApplication("refund-001")).rejects.toMatchObject({
      code: "refund_conflict",
      message: "退款申请已被处理。",
      status: 409,
    });
  });

  it("normalizes network and malformed responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(jsonResponse({ status: "unknown" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(assessRefund("order-demo-001", "1.00", "CNY")).rejects.toMatchObject({
      code: "refund_network_error",
    });
    await expect(confirmRefundApplication("refund-001")).rejects.toMatchObject({
      code: "refund_invalid_response",
    });
  });
});
