import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as assessRefund } from "./orders/[orderId]/refund-assessment/route";
import { POST as createRefund } from "./orders/[orderId]/refund-applications/route";
import { GET as getCurrentRefund } from "./orders/[orderId]/refund-application/route";
import { POST as confirmRefund } from "./refund-applications/[applicationId]/confirm/route";
import { POST as reviewRefund } from "./refund-applications/[applicationId]/review/route";

const assessment = {
  eligible_for_review: true,
  reason: "eligible_for_review",
  requires_customer_confirmation: true,
};

const application = {
  id: "refund-001",
  order_id: "order-demo-001",
  request_id: "refund-request-001",
  requested_amount: "88.00",
  currency: "CNY",
  status: "AWAITING_CUSTOMER_CONFIRMATION",
  created: true,
};

function request(body: unknown = {}) {
  return new Request("http://localhost/api/refunds", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function customerSubject(fetchMock: ReturnType<typeof vi.fn>) {
  const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
  const payload = (headers.authorization ?? "").split(".")[1] ?? "";
  return JSON.parse(Buffer.from(payload, "base64url").toString("utf8")).sub;
}

describe("V0.5 refund BFF routes", () => {
  beforeEach(() => {
    process.env.JWT_SECRET_KEY = "test-only-jwt-secret-at-least-32-bytes";
    process.env.AGENT_CORE_DEMO_USER_ID = "demo-user-li";
    process.env.AGENT_CORE_REFUND_APPROVAL_DEMO_ENABLED = "true";
    process.env.REFUND_APPROVER_USER_ID = "staff-zhang";
  });

  afterEach(() => {
    delete process.env.AGENT_CORE_DEMO_USER_ID;
    delete process.env.AGENT_CORE_REFUND_APPROVAL_DEMO_ENABLED;
    delete process.env.AGENT_CORE_URL;
    delete process.env.JWT_SECRET_KEY;
    delete process.env.REFUND_APPROVER_USER_ID;
    vi.unstubAllGlobals();
  });

  it("forwards assessment with the authenticated customer identity", async () => {
    process.env.AGENT_CORE_URL = "http://agent-core.internal:9000/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(assessment));
    vi.stubGlobal("fetch", fetchMock);

    const response = await assessRefund(
      request({ requested_amount: "88.00", requested_currency: "cny", user_id: "forged" }),
      { params: Promise.resolve({ orderId: " order-demo-001 " }) },
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(assessment);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent-core.internal:9000/v1/orders/order-demo-001/refund-assessment",
      expect.objectContaining({
        body: JSON.stringify({ requested_amount: "88.00", requested_currency: "CNY" }),
      }),
    );
    expect(customerSubject(fetchMock)).toBe("demo-user-li");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("forged");
  });

  it("creates an idempotent application and strips unknown fields", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(application));
    vi.stubGlobal("fetch", fetchMock);

    const response = await createRefund(
      request({
        request_id: " refund-request-001 ",
        requested_amount: "88.00",
        requested_currency: "CNY",
        status: "APPROVED",
      }),
      { params: Promise.resolve({ orderId: "order-demo-001" }) },
    );

    expect(response.status).toBe(200);
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(
      JSON.stringify({
        request_id: "refund-request-001",
        requested_amount: "88.00",
        requested_currency: "CNY",
      }),
    );
  });

  it("reads the current non-rejected application with customer ownership", async () => {
    const currentApplication = { ...application };
    delete (currentApplication as Partial<typeof application>).created;
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(currentApplication));
    vi.stubGlobal("fetch", fetchMock);

    const response = await getCurrentRefund(request(), {
      params: Promise.resolve({ orderId: " order-demo-001 " }),
    });

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(currentApplication);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/v1/orders/order-demo-001/refund-application",
      expect.objectContaining({
        method: "GET",
        body: undefined,
      }),
    );
    expect(customerSubject(fetchMock)).toBe("demo-user-li");
  });

  it.each([
    ["退款申请不存在。", "refund_not_found"],
    ["订单不存在。", "refund_not_found"],
  ] as const)("preserves the authoritative GET 404 detail: %s", async (detail, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail }, 404)),
    );

    const response = await getCurrentRefund(request(), {
      params: Promise.resolve({ orderId: "order-demo-001" }),
    });

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: { code, message: detail } });
  });

  it("does not turn a GET service failure into an empty application", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({ detail: "退款服务暂时不可用，请稍后重试。" }, 503),
      ),
    );

    const response = await getCurrentRefund(request(), {
      params: Promise.resolve({ orderId: "order-demo-001" }),
    });

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: {
        code: "refund_service_unavailable",
        message: "退款服务暂时不可用，请稍后重试。",
      },
    });
  });

  it("confirms as the customer and reviews as the configured approver", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({ ...application, created: undefined, status: "PENDING_MANUAL_APPROVAL" }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          ...application,
          created: undefined,
          status: "APPROVED",
          reviewed_by_user_id: "staff-zhang",
          reviewed_at: "2026-08-23T10:00:00Z",
          review_note: "已核对",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const confirmation = await confirmRefund(request(), {
      params: Promise.resolve({ applicationId: "refund-001" }),
    });
    const review = await reviewRefund(
      request({ decision: "APPROVED", review_note: "  已核对  ", user_id: "forged" }),
      { params: Promise.resolve({ applicationId: "refund-001" }) },
    );

    expect(confirmation.status).toBe(200);
    expect(review.status).toBe(200);
    expect(customerSubject(fetchMock)).toBe("demo-user-li");
    const reviewHeaders = fetchMock.mock.calls[1]?.[1]?.headers as Record<string, string>;
    const reviewPayload = (reviewHeaders.authorization ?? "").split(".")[1] ?? "";
    expect(JSON.parse(Buffer.from(reviewPayload, "base64url").toString("utf8")).sub).toBe(
      "demo-user-li",
    );
    expect(fetchMock.mock.calls[1]?.[1]?.body).toBe(
      JSON.stringify({ decision: "APPROVED", review_note: "已核对" }),
    );
  });

  it.each([
    [401, { detail: "访问令牌无效或已过期。secret" }, "refund_unauthorized"],
    [403, { detail: "无权审批退款申请。secret" }, "refund_forbidden"],
    [404, { detail: "退款申请不存在。" }, "refund_not_found"],
    [409, { detail: "退款申请已被其他审批人处理，请刷新后重试。" }, "refund_conflict"],
  ] as const)("maps upstream %i to stable %s", async (status, body, code) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body, status)));

    const response = await reviewRefund(request({ decision: "REJECTED" }), {
      params: Promise.resolve({ applicationId: "refund-001" }),
    });

    expect(response.status).toBe(status);
    const responseBody = await response.json();
    expect(responseBody).toMatchObject({ error: { code } });
    expect(JSON.stringify(responseBody)).not.toContain("secret");
  });

  it("translates backend risk reasons without approving in the BFF", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({ detail: "amount_exceeds_order_total" }, 422),
      ),
    );

    const response = await createRefund(
      request({
        request_id: "refund-request-001",
        requested_amount: "300.00",
        requested_currency: "CNY",
      }),
      { params: Promise.resolve({ orderId: "order-demo-001" }) },
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({
      error: { code: "refund_ineligible", message: "退款金额不能超过订单金额。" },
    });
  });

  it("rejects invalid requests before contacting the backend", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const invalid = await assessRefund(
      request({ requested_amount: "not-money", requested_currency: "CNY" }),
      { params: Promise.resolve({ orderId: "../unsafe" }) },
    );

    expect(invalid.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("uses the current session and leaves approval authorization to Agent Core", async () => {
    delete process.env.AGENT_CORE_REFUND_APPROVAL_DEMO_ENABLED;
    delete process.env.REFUND_APPROVER_USER_ID;
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ ...application, status: "APPROVED", created: undefined }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await reviewRefund(request({ decision: "APPROVED" }), {
      params: Promise.resolve({ applicationId: "refund-001" }),
    });

    expect(response.status).toBe(200);
    expect(customerSubject(fetchMock)).toBe("demo-user-li");
  });

  it("normalizes network, timeout, and non-JSON upstream failures", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("internal host"))
      .mockRejectedValueOnce(new DOMException("timed out", "TimeoutError"))
      .mockResolvedValueOnce(new Response("database secret", { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);

    const context = { params: Promise.resolve({ applicationId: "refund-001" }) };
    const network = await confirmRefund(request(), context);
    const timeout = await confirmRefund(request(), context);
    const invalid = await confirmRefund(request(), context);

    expect(network.status).toBe(503);
    expect(await network.json()).toMatchObject({ error: { code: "refund_upstream_unreachable" } });
    expect(timeout.status).toBe(504);
    expect(await timeout.json()).toMatchObject({ error: { code: "refund_upstream_timeout" } });
    expect(invalid.status).toBe(502);
    expect(await invalid.text()).not.toContain("database secret");
  });
});
