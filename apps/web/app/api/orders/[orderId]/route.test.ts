import { afterEach, describe, expect, it, vi } from "vitest";

import type { OrderDetail } from "@/types/order";

import { GET } from "./route";

const orderFixture: OrderDetail = {
  id: "order-demo-001",
  order_number: "EC-20260810-001",
  status: "shipped",
  total_amount: "299.00",
  currency: "CNY",
  created_at: "2026-08-10T08:30:00Z",
  shipment_events: [
    {
      id: "shipment-event-001",
      status: "confirmed",
      description: "商家已确认订单",
      location: "杭州市",
      occurred_at: "2026-08-08T01:15:00Z",
    },
  ],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function callRoute(orderId: string) {
  return GET(new Request("http://localhost/api/orders/unused"), {
    params: Promise.resolve({ orderId }),
  });
}

describe("GET /api/orders/[orderId]", () => {
  afterEach(() => {
    delete process.env.AGENT_CORE_URL;
    vi.unstubAllGlobals();
  });

  it("encodes the order ID and forwards only the path to Agent Core", async () => {
    process.env.AGENT_CORE_URL = "http://agent-core.internal:9000/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ ...orderFixture, id: "order demo-001" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await callRoute("  order demo-001  ");

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ id: "order demo-001" });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent-core.internal:9000/v1/orders/order%20demo-001",
      expect.objectContaining({
        method: "GET",
        cache: "no-store",
        signal: expect.any(AbortSignal),
      }),
    );
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("user_id");
  });

  it("uses the localhost Agent Core default", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(orderFixture));
    vi.stubGlobal("fetch", fetchMock);

    await callRoute("order-demo-001");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/v1/orders/order-demo-001",
      expect.any(Object),
    );
  });

  it("rejects empty, overlong, and unsafe order IDs before fetching", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const empty = await callRoute("   ");
    const overlong = await callRoute("a".repeat(101));
    const unsafe = await callRoute("../other-order");

    expect(empty.status).toBe(400);
    expect(overlong.status).toBe(400);
    expect(unsafe.status).toBe(400);
    expect(await empty.json()).toMatchObject({
      error: { code: "order_invalid_request" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    [404, "order_not_found", "订单不存在。"],
    [503, "order_service_unavailable", "订单服务暂时不可用，请稍后重试。"],
  ])("maps upstream %i to a stable %s error", async (status, code, detail) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ detail }, status)),
    );

    const response = await callRoute("order-demo-001");

    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ error: { code, message: detail } });
  });

  it("returns a stable error when Agent Core is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")));

    const response = await callRoute("order-demo-001");

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: {
        code: "order_upstream_unreachable",
        message: "无法连接订单服务，请稍后重试。",
      },
    });
  });

  it("distinguishes an upstream timeout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockRejectedValue(new DOMException("timed out", "TimeoutError")),
    );

    const response = await callRoute("order-demo-001");

    expect(response.status).toBe(504);
    expect(await response.json()).toMatchObject({
      error: { code: "order_upstream_timeout" },
    });
  });

  it("rejects non-JSON and malformed successful upstream responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("gateway error", { status: 502 }))
      .mockResolvedValueOnce(jsonResponse({ ...orderFixture, total_amount: 299 }));
    vi.stubGlobal("fetch", fetchMock);

    const nonJson = await callRoute("order-demo-001");
    const malformed = await callRoute("order-demo-001");

    expect(nonJson.status).toBe(502);
    expect(malformed.status).toBe(502);
    expect(await nonJson.json()).toMatchObject({
      error: { code: "order_invalid_upstream_response" },
    });
    expect(await malformed.json()).toMatchObject({
      error: { code: "order_invalid_upstream_response" },
    });
  });
});
