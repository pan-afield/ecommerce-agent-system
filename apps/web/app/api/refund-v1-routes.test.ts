import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST as executeRefund } from "./refund-applications/[applicationId]/execute/route";
import { GET as getExecution } from "./refund-applications/[applicationId]/execution/route";
import { POST as recoverRefund } from "./refund-applications/[applicationId]/recover/route";
import { GET as listOperations } from "./refund-operations/route";
import { GET as getOperation } from "./refund-operations/[applicationId]/route";
import { POST as resumeOperation } from "./refund-operations/[applicationId]/resume/route";
import { POST as resubmitOperation } from "./refund-operations/[applicationId]/resubmit/route";

const execution = {
  id: "execution-001",
  status: "PROCESSING",
  amount: "88.00",
  currency: "CNY",
  provider_reference: null,
};

const queue = {
  items: [
    {
      refund_application_id: "refund-001",
      execution_status: "RUNNING",
      status: "MANUAL_REQUIRED",
      attempts: 5,
      last_error_code: "NOT_FOUND",
      updated_at: "2026-09-19T08:00:00Z",
    },
  ],
};

const detail = {
  execution,
  recovery: {
    status: "MANUAL_REQUIRED",
    attempts: 5,
    next_attempt_at: "2026-09-19T08:01:00Z",
    last_error_code: "NOT_FOUND",
    updated_at: "2026-09-19T08:00:00Z",
  },
  events: [
    {
      id: "9007199254740993",
      action: "MANUAL_REQUIRED",
      source: "compensation",
      actor_user_id: null,
      source_event_id: null,
      from_status: null,
      to_status: null,
      provider_reference: null,
      error_code: "NOT_FOUND",
      note: null,
      created_at: "2026-09-19T08:00:00Z",
    },
  ],
  next_after_id: "9007199254740993",
};

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

function postRequest(body: unknown = {}) {
  return new Request("http://localhost/api/refunds", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("V1.0 refund BFF routes", () => {
  beforeEach(() => {
    process.env.JWT_SECRET_KEY = "test-only-jwt-secret-at-least-32-bytes";
    process.env.AGENT_CORE_DEMO_USER_ID = "demo-user-li";
  });

  afterEach(() => {
    delete process.env.AGENT_CORE_URL;
    delete process.env.AGENT_CORE_DEMO_USER_ID;
    delete process.env.JWT_SECRET_KEY;
    vi.unstubAllGlobals();
  });

  it("forwards customer execute, status, and recover without browser-controlled money", async () => {
    process.env.AGENT_CORE_URL = "http://agent-core.internal:9000/";
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse(execution));
    vi.stubGlobal("fetch", fetchMock);
    const context = { params: Promise.resolve({ applicationId: " refund-001 " }) };

    expect((await executeRefund(postRequest({ amount: "999" }), context)).status).toBe(200);
    expect((await getExecution(postRequest(), context)).status).toBe(200);
    expect((await recoverRefund(postRequest(), context)).status).toBe(200);

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "http://agent-core.internal:9000/v1/refund-applications/refund-001/execute",
      "http://agent-core.internal:9000/v1/refund-applications/refund-001/execution",
      "http://agent-core.internal:9000/v1/refund-applications/refund-001/recover",
    ]);
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBeUndefined();
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("999");
  });

  it("keeps execution timeout as an unknown outcome", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockRejectedValue(new DOMException("secret", "TimeoutError")),
    );

    const response = await executeRefund(postRequest(), {
      params: Promise.resolve({ applicationId: "refund-001" }),
    });

    expect(response.status).toBe(504);
    expect(await response.json()).toEqual({
      error: {
        code: "refund_outcome_unknown",
        message: "退款结果暂时未知，请查询执行状态。",
      },
    });
  });

  it("preserves authoritative missing execution and service errors", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "无记录" }, 404))
      .mockResolvedValueOnce(jsonResponse({ detail: "退款服务暂时不可用，请稍后重试。" }, 503));
    vi.stubGlobal("fetch", fetchMock);
    const context = { params: Promise.resolve({ applicationId: "refund-001" }) };

    const missing = await getExecution(postRequest(), context);
    const unavailable = await getExecution(postRequest(), context);
    expect(missing.status).toBe(404);
    expect(await missing.json()).toEqual({ error: { code: "refund_not_found", message: "无记录" } });
    expect(unavailable.status).toBe(503);
    expect(await unavailable.json()).toMatchObject({ error: { code: "refund_service_unavailable" } });
  });

  it("validates and forwards the admin queue and string audit cursor", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(queue))
      .mockResolvedValueOnce(jsonResponse(detail));
    vi.stubGlobal("fetch", fetchMock);

    const list = await listOperations(new Request("http://localhost/api/refund-operations?limit=50"));
    const item = await getOperation(
      new Request("http://localhost/api/refund-operations/refund-001?after_id=9007199254740993&limit=50"),
      { params: Promise.resolve({ applicationId: "refund-001" }) },
    );

    expect(list.status).toBe(200);
    expect(item.status).toBe(200);
    expect((await item.json()).next_after_id).toBe("9007199254740993");
    expect(fetchMock.mock.calls[1]?.[0]).toContain("after_id=9007199254740993");
  });

  it("requires a bounded note and refreshes no state inside the BFF", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const context = { params: Promise.resolve({ applicationId: "refund-001" }) };

    const invalid = await resumeOperation(postRequest({ note: "   " }), context);
    const valid = await resubmitOperation(postRequest({ note: "  已核实沙箱没有原请求  ", amount: "999" }), context);

    expect(invalid.status).toBe(400);
    expect(valid.status).toBe(204);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[1]?.body).toBe(JSON.stringify({ note: "已核实沙箱没有原请求" }));
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("999");
  });

  it.each([
    [401, "访问令牌无效或已过期。secret", "refund_unauthorized", false],
    [403, "无权审批退款申请。secret", "refund_forbidden", false],
    [404, "退款执行记录不存在。", "refund_not_found", true],
    [409, "当前退款不允许重新开启核对。", "refund_conflict", true],
    [503, "secret-503", "refund_service_unavailable", false],
  ] as const)("maps admin operation HTTP %i without leaking internal failures", async (status, detailMessage, code, preservesDetail) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail: detailMessage }, status)),
    );
    const response = await resumeOperation(postRequest({ note: "已核实" }), {
      params: Promise.resolve({ applicationId: "refund-001" }),
    });
    expect(response.status).toBe(status);
    const body = await response.json();
    expect(body).toMatchObject({ error: { code } });
    if (preservesDetail) {
      expect(body.error.message).toBe(detailMessage);
    } else {
      expect(JSON.stringify(body)).not.toContain("secret");
    }
  });
});
