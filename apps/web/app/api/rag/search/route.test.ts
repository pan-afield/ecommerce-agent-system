import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GET } from "./route";

function request(url = "http://localhost/api/rag/search?query=退款政策&limit=3") {
  return new Request(url, { method: "GET" });
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const citation = {
  source_id: "refund-policy-v1",
  chunk_id: "a".repeat(64),
  page_number: 2,
  content: "退款需要订单本人提交。",
  score: 0.25,
};

describe("GET /api/rag/search", () => {
  beforeEach(() => {
    process.env.JWT_SECRET_KEY = "test-only-jwt-secret-at-least-32-bytes";
    process.env.AGENT_CORE_DEMO_USER_ID = "demo-user-li";
  });

  afterEach(() => {
    vi.useRealTimers();
    delete process.env.AGENT_CORE_URL;
    delete process.env.AGENT_CORE_DEMO_USER_ID;
    delete process.env.JWT_SECRET_KEY;
    vi.unstubAllGlobals();
  });

  it("forwards an authenticated, encoded query and returns citations", async () => {
    process.env.AGENT_CORE_URL = "http://agent-core.internal:9000/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ query: "退款政策", citations: [citation] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET(request("http://localhost/api/rag/search?query=%20退款政策%20&limit=5"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ query: "退款政策", citations: [citation] });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent-core.internal:9000/v1/rag/search?query=%E9%80%80%E6%AC%BE%E6%94%BF%E7%AD%96&limit=5",
      expect.objectContaining({
        method: "GET",
        signal: expect.any(AbortSignal),
        headers: expect.objectContaining({ authorization: expect.stringMatching(/^Bearer /) }),
      }),
    );
  });

  it("returns an empty citation list as a valid no-result response", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ query: "不存在", citations: [] })));

    const response = await GET(request("http://localhost/api/rag/search?query=不存在"));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ query: "不存在", citations: [] });
  });

  it("preserves the stable forbidden response without exposing role internals", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "rag_forbidden", message: "当前用户没有知识库访问权限。" } },
          403,
        ),
      ),
    );

    const response = await GET(request());

    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({
      error: { code: "rag_forbidden", message: "当前用户没有知识库访问权限。" },
    });
  });

  it("preserves stable backend errors and sanitizes authentication details", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ error: { code: "rag_not_configured", message: "知识库检索服务尚未配置。" } }, 503))
      .mockResolvedValueOnce(jsonResponse({ detail: "访问令牌无效 internal-token=secret" }, 401));
    vi.stubGlobal("fetch", fetchMock);

    const unavailable = await GET(request());
    const unauthorized = await GET(request());

    expect(unavailable.status).toBe(503);
    expect(await unavailable.json()).toEqual({ error: { code: "rag_not_configured", message: "知识库检索服务尚未配置。" } });
    expect(unauthorized.status).toBe(401);
    expect(await unauthorized.json()).toEqual({ error: { code: "rag_unauthorized", message: "登录状态无效，请重新登录。" } });
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("internal-token");
  });

  it.each([
    ["rag_embedding_unavailable", "知识库向量服务暂时不可用。"],
    ["rag_database_incompatible", "知识库向量数据库配置不兼容。"],
  ] as const)("preserves the local embedding error %s", async (code, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({ error: { code, message } }, 503),
      ),
    );

    const response = await GET(request());

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ error: { code, message } });
  });

  it("normalizes network, timeout-like, and non-JSON upstream failures", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValueOnce(new Response("not json", { status: 503 }))
      .mockResolvedValueOnce(jsonResponse({ query: "坏响应", citations: [{ bad: true }] }));
    vi.stubGlobal("fetch", fetchMock);

    const unreachable = await GET(request());
    const nonJson = await GET(request());
    const malformed = await GET(request());

    expect(unreachable.status).toBe(503);
    expect(await unreachable.json()).toMatchObject({ error: { code: "rag_upstream_unreachable" } });
    expect(nonJson.status).toBe(502);
    expect(await nonJson.json()).toMatchObject({ error: { code: "rag_invalid_upstream_response" } });
    expect(malformed.status).toBe(502);
    expect(await malformed.json()).toMatchObject({ error: { code: "rag_invalid_upstream_response" } });
  });

  it("returns a stable timeout error when Agent Core does not respond", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn<typeof fetch>().mockImplementation((_input, init) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("timeout", "AbortError")), {
          once: true,
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const pending = GET(request());
    await vi.advanceTimersByTimeAsync(8_000);
    const response = await pending;
    vi.useRealTimers();

    expect(response.status).toBe(504);
    expect(await response.json()).toEqual({
      error: { code: "rag_upstream_timeout", message: "知识库响应超时，请稍后重试。" },
    });
  });

  it("rejects blank and out-of-range query parameters before calling Agent Core", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const blank = await GET(request("http://localhost/api/rag/search?query=%20"));
    const tooLong = await GET(request(`http://localhost/api/rag/search?query=${"x".repeat(501)}`));
    const badLimit = await GET(request("http://localhost/api/rag/search?query=test&limit=11"));

    expect(blank.status).toBe(400);
    expect(tooLong.status).toBe(400);
    expect(badLimit.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
