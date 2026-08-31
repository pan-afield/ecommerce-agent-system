import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

function createRequest(body: unknown) {
  return new Request("http://localhost/api/chat/stream", {
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

function streamResponse(body = "event: assistant\ndata: {\"content\":\"您好\",\"model\":\"test-model\"}\n\nevent: done\ndata: {}\n\n") {
  return new Response(body, {
    headers: { "content-type": "text/event-stream; charset=utf-8" },
  });
}

describe("POST /api/chat/stream", () => {
  beforeEach(() => {
    process.env.JWT_SECRET_KEY = "test-only-jwt-secret-at-least-32-bytes";
    process.env.AGENT_CORE_DEMO_USER_ID = "demo-user-li";
  });

  afterEach(() => {
    delete process.env.AGENT_CORE_DEMO_USER_ID;
    delete process.env.AGENT_CORE_URL;
    delete process.env.JWT_SECRET_KEY;
    vi.unstubAllGlobals();
  });

  it("authenticates, normalizes, and pipes the upstream SSE response", async () => {
    process.env.AGENT_CORE_URL = "http://agent-core.internal:9000/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(streamResponse());
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      createRequest({
        message: "  查询订单  ",
        thread_id: "  thread-1  ",
        request_id: "  request-1  ",
        user_id: "forged-user",
      }),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    expect(response.headers.get("x-accel-buffering")).toBe("no");
    expect(await response.text()).toContain("event: assistant");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent-core.internal:9000/v1/chat/stream",
      expect.objectContaining({
        body: JSON.stringify({
          message: "查询订单",
          thread_id: "thread-1",
          request_id: "request-1",
        }),
        headers: expect.objectContaining({
          accept: "text/event-stream",
          authorization: expect.stringMatching(/^Bearer /),
        }),
        signal: expect.any(AbortSignal),
      }),
    );
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("forged-user");
  });

  it("rejects invalid request data before calling Agent Core", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(createRequest({ message: " ", request_id: "orphan" }));

    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({
      error: { code: "chat_invalid_request" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("fails closed when server-side JWT configuration is missing", async () => {
    delete process.env.JWT_SECRET_KEY;
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(createRequest({ message: "你好" }));

    expect(response.status).toBe(503);
    expect(await response.json()).toMatchObject({
      error: { code: "chat_auth_unavailable" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves stable chat errors returned before streaming starts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "chat_rate_limited", message: "请求过于频繁，请稍后重试。" } },
          429,
        ),
      ),
    );

    const response = await POST(createRequest({ message: "你好" }));

    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({
      error: { code: "chat_rate_limited", message: "请求过于频繁，请稍后重试。" },
    });
  });

  it("preserves stable RAG embedding errors before streaming starts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "rag_database_incompatible", message: "知识库向量数据库配置不兼容。" } },
          503,
        ),
      ),
    );

    const response = await POST(createRequest({ message: "退款政策" }));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: { code: "rag_database_incompatible", message: "知识库向量数据库配置不兼容。" },
    });
  });

  it("sanitizes upstream authentication failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({ detail: "访问令牌无效或已过期。internal-token=secret" }, 401),
      ),
    );

    const response = await POST(createRequest({ message: "你好" }));

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({
      error: { code: "chat_unauthorized", message: "登录状态无效，请重新登录。" },
    });
  });

  it("normalizes network and invalid upstream responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline internal host"))
      .mockResolvedValueOnce(new Response("not sse", { status: 200 }))
      .mockResolvedValueOnce(new Response("gateway failure", { status: 502 }));
    vi.stubGlobal("fetch", fetchMock);

    const unreachable = await POST(createRequest({ message: "第一次" }));
    const nonSse = await POST(createRequest({ message: "第二次" }));
    const nonJsonError = await POST(createRequest({ message: "第三次" }));

    expect(unreachable.status).toBe(503);
    expect(await unreachable.json()).toMatchObject({
      error: { code: "chat_upstream_unreachable" },
    });
    expect(nonSse.status).toBe(502);
    expect(await nonSse.json()).toMatchObject({
      error: { code: "chat_invalid_upstream_response" },
    });
    expect(nonJsonError.status).toBe(502);
    expect(await nonJsonError.json()).toMatchObject({
      error: { code: "chat_invalid_upstream_response" },
    });
  });
});
