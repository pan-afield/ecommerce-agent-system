import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_SESSION_EXPIRED_EVENT,
  authenticatedFetch,
} from "./authenticated-fetch";

function response(status: number, body = "") {
  return new Response(body, { status });
}

describe("authenticatedFetch", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("refreshes an expired session and retries the original request once", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(200, '{"ok":true}'))
      .mockResolvedValueOnce(response(200, '{"query":"退款"}'));
    vi.stubGlobal("fetch", fetchMock);

    const result = await authenticatedFetch("/api/rag/search?query=退款", {
      method: "GET",
      cache: "no-store",
    });

    expect(result.status).toBe(200);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/rag/search?query=退款",
      expect.objectContaining({ method: "GET" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/auth/refresh",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/api/rag/search?query=退款",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("announces an expired session and does not retry when refresh fails", async () => {
    const onExpired = vi.fn();
    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpired);
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(401));
    vi.stubGlobal("fetch", fetchMock);

    const result = await authenticatedFetch("/api/orders/order-demo-001");

    expect(result.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(onExpired).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, onExpired);
  });

  it("shares one refresh request across concurrent 401 responses", async () => {
    let finishRefresh: ((response: Response) => void) | undefined;
    const refreshResponse = new Promise<Response>((resolve) => {
      finishRefresh = resolve;
    });
    let resourceRequests = 0;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      if (input === "/api/auth/refresh") {
        return refreshResponse;
      }

      resourceRequests += 1;
      return Promise.resolve(response(resourceRequests <= 2 ? 401 : 200));
    });
    vi.stubGlobal("fetch", fetchMock);

    const ragRequest = authenticatedFetch("/api/rag/search?query=退款");
    const orderRequest = authenticatedFetch("/api/orders/order-demo-001");

    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) => input === "/api/auth/refresh"),
      ).toHaveLength(1);
    });
    finishRefresh?.(response(200, '{"ok":true}'));

    await expect(Promise.all([ragRequest, orderRequest])).resolves.toEqual([
      expect.objectContaining({ status: 200 }),
      expect.objectContaining({ status: 200 }),
    ]);
    expect(resourceRequests).toBe(4);
  });

  it("does not start another refresh for a late 401 from the previous session generation", async () => {
    let finishRefresh: ((response: Response) => void) | undefined;
    let finishLateUnauthorized: ((response: Response) => void) | undefined;
    const refreshResponse = new Promise<Response>((resolve) => {
      finishRefresh = resolve;
    });
    const lateUnauthorized = new Promise<Response>((resolve) => {
      finishLateUnauthorized = resolve;
    });
    let resourceRequests = 0;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((input) => {
      if (input === "/api/auth/refresh") {
        return refreshResponse;
      }

      resourceRequests += 1;
      if (resourceRequests === 1) return Promise.resolve(response(401));
      if (resourceRequests === 2) return lateUnauthorized;
      return Promise.resolve(response(200));
    });
    vi.stubGlobal("fetch", fetchMock);

    const ragRequest = authenticatedFetch("/api/rag/search?query=退款");
    const orderRequest = authenticatedFetch("/api/orders/order-demo-001");
    await vi.waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([input]) => input === "/api/auth/refresh"),
      ).toHaveLength(1);
    });

    finishRefresh?.(response(200, '{"ok":true}'));
    await expect(ragRequest).resolves.toEqual(expect.objectContaining({ status: 200 }));
    finishLateUnauthorized?.(response(401));
    await expect(orderRequest).resolves.toEqual(expect.objectContaining({ status: 200 }));

    expect(
      fetchMock.mock.calls.filter(([input]) => input === "/api/auth/refresh"),
    ).toHaveLength(1);
    expect(resourceRequests).toBe(4);
  });

  it.each([403, 429])("does not refresh a %s response", async (status) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response(status));
    vi.stubGlobal("fetch", fetchMock);

    const result = await authenticatedFetch("/api/rag/search?query=退款");

    expect(result.status).toBe(status);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not enter a refresh loop when the retried SSE request is still unauthorized", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(200, '{"ok":true}'))
      .mockResolvedValueOnce(response(401))
      .mockResolvedValueOnce(response(200));
    vi.stubGlobal("fetch", fetchMock);

    const result = await authenticatedFetch("/api/chat/stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ message: "你好" }),
    });

    expect(result.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(
      fetchMock.mock.calls.filter(([input]) => input === "/api/auth/refresh"),
    ).toHaveLength(1);
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/auth/logout",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
