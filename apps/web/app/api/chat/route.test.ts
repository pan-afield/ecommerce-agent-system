import { afterEach, describe, expect, it, vi } from "vitest";

import { POST } from "./route";

function createRequest(body: string, contentType = "application/json") {
  return {
    headers: new Headers({ "content-type": contentType }),
    json: vi.fn(async () => JSON.parse(body) as unknown),
  } as unknown as Request;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("POST /api/chat", () => {
  afterEach(() => {
    delete process.env.AGENT_CORE_URL;
    vi.unstubAllGlobals();
  });

  it("forwards a trimmed message to the configured Agent Core", async () => {
    process.env.AGENT_CORE_URL = "http://agent-core.internal:9000/";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ assistant: { content: "您好" }, model: "test-model" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(createRequest(JSON.stringify({ message: "  你好  " })));

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      assistant: { content: "您好" },
      model: "test-model",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://agent-core.internal:9000/v1/chat",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ message: "你好" }),
        cache: "no-store",
      }),
    );
  });

  it("uses localhost Agent Core when AGENT_CORE_URL is not configured", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ assistant: { content: "默认地址可用" }, model: "test-model" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await POST(createRequest(JSON.stringify({ message: "你好" })));

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/v1/chat",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("normalizes and forwards thread and request identifiers", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ assistant: { content: "上下文已恢复" }, model: "test-model" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await POST(
      createRequest(
        JSON.stringify({
          message: "  继续  ",
          thread_id: "  thread-1  ",
          request_id: "  request-1  ",
          user_id: "forged-user",
        }),
      ),
    );

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/v1/chat",
      expect.objectContaining({
        body: JSON.stringify({
          message: "继续",
          thread_id: "thread-1",
          request_id: "request-1",
        }),
      }),
    );
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("forged-user");
  });

  it("allows a thread without a request identifier", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ assistant: { content: "收到" }, model: "test-model" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await POST(
      createRequest(JSON.stringify({ message: "你好", thread_id: "thread-1" })),
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/v1/chat",
      expect.objectContaining({
        body: JSON.stringify({ message: "你好", thread_id: "thread-1" }),
      }),
    );
  });

  it("rejects non-JSON and malformed JSON requests", async () => {
    const wrongType = await POST(createRequest("{}", "text/plain"));
    const malformed = await POST(createRequest("{"));

    expect(wrongType.status).toBe(415);
    expect(await wrongType.json()).toMatchObject({ error: { code: "chat_invalid_request" } });
    expect(malformed.status).toBe(400);
    expect(await malformed.json()).toMatchObject({ error: { code: "chat_invalid_request" } });
  });

  it("rejects empty and overlong messages before calling Agent Core", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const empty = await POST(createRequest(JSON.stringify({ message: "   " })));
    const overlong = await POST(createRequest(JSON.stringify({ message: "a".repeat(2_001) })));

    expect(empty.status).toBe(400);
    expect(overlong.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects invalid context identifiers before calling Agent Core", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);

    const blankThread = await POST(
      createRequest(JSON.stringify({ message: "你好", thread_id: "   " })),
    );
    const overlongRequest = await POST(
      createRequest(
        JSON.stringify({
          message: "你好",
          thread_id: "thread-1",
          request_id: "r".repeat(129),
        }),
      ),
    );
    const invalidType = await POST(
      createRequest(JSON.stringify({ message: "你好", thread_id: 42 })),
    );
    const unscopedRequest = await POST(
      createRequest(JSON.stringify({ message: "你好", request_id: "request-1" })),
    );

    expect(blankThread.status).toBe(400);
    expect(overlongRequest.status).toBe(400);
    expect(invalidType.status).toBe(400);
    expect(unscopedRequest.status).toBe(400);
    expect(await unscopedRequest.json()).toMatchObject({
      error: { code: "chat_invalid_request" },
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("preserves stable upstream error codes and messages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "chat_rate_limited", message: "请求过于频繁，请稍后重试。" } },
          429,
        ),
      ),
    );

    const response = await POST(createRequest(JSON.stringify({ message: "你好" })));

    expect(response.status).toBe(429);
    expect(await response.json()).toEqual({
      error: { code: "chat_rate_limited", message: "请求过于频繁，请稍后重试。" },
    });
  });

  it("returns a stable proxy error when Agent Core is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")));

    const response = await POST(createRequest(JSON.stringify({ message: "你好" })));

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: {
        code: "chat_upstream_unreachable",
        message: "无法连接客服服务，请稍后重试。",
      },
    });
  });

  it("rejects non-JSON and malformed successful upstream responses", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(new Response("gateway error", { status: 502 }))
      .mockResolvedValueOnce(jsonResponse({ assistant: { content: 42 }, model: "test" }));
    vi.stubGlobal("fetch", fetchMock);

    const nonJson = await POST(createRequest(JSON.stringify({ message: "第一次" })));
    const malformed = await POST(createRequest(JSON.stringify({ message: "第二次" })));

    expect(nonJson.status).toBe(502);
    expect(await nonJson.json()).toMatchObject({
      error: { code: "chat_invalid_upstream_response" },
    });
    expect(malformed.status).toBe(502);
    expect(await malformed.json()).toMatchObject({
      error: { code: "chat_invalid_upstream_response" },
    });
  });
});
