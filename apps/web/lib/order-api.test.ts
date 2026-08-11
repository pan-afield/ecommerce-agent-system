import { afterEach, describe, expect, it, vi } from "vitest";

import type { OrderDetail } from "@/types/order";

import { getOrder, type OrderApiError } from "./order-api";

const orderFixture: OrderDetail = {
  id: "order-demo-001",
  order_number: "EC-20260810-001",
  status: "shipped",
  total_amount: "299.00",
  currency: "CNY",
  created_at: "2026-08-10T08:30:00Z",
  shipment_events: [],
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("getOrder", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a valid order from the same-origin BFF", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(orderFixture));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getOrder("  order demo-001  ")).resolves.toEqual(orderFixture);
    expect(fetchMock).toHaveBeenCalledWith("/api/orders/order%20demo-001", {
      method: "GET",
      cache: "no-store",
    });
  });

  it("throws the stable error returned by the BFF", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "order_not_found", message: "订单不存在。" } },
          404,
        ),
      ),
    );

    await expect(getOrder("order-demo-002")).rejects.toMatchObject({
      code: "order_not_found",
      message: "订单不存在。",
      status: 404,
    });
  });

  it("rejects empty input without sending a request", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    await expect(getOrder("   ")).rejects.toMatchObject({
      code: "order_invalid_request",
      status: 400,
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("normalizes a browser network failure", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")));

    await expect(getOrder("order-demo-001")).rejects.toEqual(
      expect.objectContaining<Partial<OrderApiError>>({
        code: "order_network_error",
        status: 0,
      }),
    );
  });

  it("rejects non-JSON and malformed successful responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("not json", { status: 200 }))
      .mockResolvedValueOnce(jsonResponse({ ...orderFixture, shipment_events: null }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getOrder("order-demo-001")).rejects.toMatchObject({
      code: "order_invalid_response",
      status: 200,
    });
    await expect(getOrder("order-demo-001")).rejects.toMatchObject({
      code: "order_invalid_response",
      status: 200,
    });
  });
});
