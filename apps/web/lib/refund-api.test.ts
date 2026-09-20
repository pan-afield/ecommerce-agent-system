import { afterEach, describe, expect, it, vi } from "vitest";

import type { RefundApplication, RefundAssessment, RefundOperationDetail } from "@/types/refund";

import {
  assessRefund,
  confirmRefundApplication,
  createRefundApplication,
  executeRefund,
  getCurrentRefundApplication,
  getRefundExecution,
  getRefundOperation,
  getRefundOperations,
  recoverRefundExecution,
  reviewRefundApplication,
  runRefundOperation,
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

const execution = {
  id: "execution-001",
  status: "PROCESSING" as const,
  amount: "88.00",
  currency: "CNY",
  provider_reference: null,
};

const operationDetail: RefundOperationDetail = {
  execution,
  recovery: {
    status: "MANUAL_REQUIRED",
    attempts: 5,
    next_attempt_at: "2026-09-19T08:01:00Z",
    last_error_code: "NOT_FOUND",
    updated_at: "2026-09-19T08:00:00Z",
  },
  events: [],
  next_after_id: "9007199254740993",
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

  it("refreshes an expired session before retrying a refund request", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "refund_unauthorized", message: "登录状态无效。" } }, 401))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse(assessment));
    vi.stubGlobal("fetch", fetchMock);

    await expect(assessRefund("order-demo-001", "88.00", "CNY")).resolves.toEqual(
      assessment,
    );
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/orders/order-demo-001/refund-assessment",
      "/api/auth/refresh",
      "/api/orders/order-demo-001/refund-assessment",
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

  it("uses the execution and recovery endpoints without sending amount or an idempotency key", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(execution));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRefundExecution("refund-001")).resolves.toEqual(execution);
    await expect(executeRefund("refund-001")).resolves.toEqual(execution);
    await expect(recoverRefundExecution("refund-001")).resolves.toEqual(execution);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/refund-applications/refund-001/execution",
      "/api/refund-applications/refund-001/execute",
      "/api/refund-applications/refund-001/recover",
    ]);
    expect(fetchMock.mock.calls.every(([, init]) => init?.body === undefined)).toBe(true);
  });

  it("validates admin queue, detail, and string audit cursors", async () => {
    const queue = {
      items: [{
        refund_application_id: "refund-001",
        execution_status: "RUNNING",
        status: "MANUAL_REQUIRED",
        attempts: 5,
        last_error_code: "NOT_FOUND",
        updated_at: "2026-09-19T08:00:00Z",
      }],
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(queue))
      .mockResolvedValueOnce(jsonResponse(operationDetail));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRefundOperations()).resolves.toEqual(queue);
    await expect(getRefundOperation("refund-001", "9007199254740993")).resolves.toEqual(operationDetail);
    expect(fetchMock.mock.calls[1]?.[0]).toContain("after_id=9007199254740993");
  });

  it("rejects malformed execution and audit responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ ...execution, status: "UNKNOWN" }))
      .mockResolvedValueOnce(jsonResponse({ ...operationDetail, next_after_id: 12 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getRefundExecution("refund-001")).rejects.toMatchObject({ code: "refund_invalid_response" });
    await expect(getRefundOperation("refund-001")).rejects.toMatchObject({ code: "refund_invalid_response" });
  });

  it("submits only an admin note and preserves stable write errors", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ error: { code: "refund_conflict", message: "当前状态不允许此操作。" } }, 409));
    vi.stubGlobal("fetch", fetchMock);

    await expect(runRefundOperation("refund-001", "resume", "已核实" )).resolves.toBeUndefined();
    await expect(runRefundOperation("refund-001", "resubmit", "确认原键" )).rejects.toMatchObject({ code: "refund_conflict", status: 409 });
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ note: "已核实" }));
  });
});
