import { afterEach, describe, expect, it, vi } from "vitest";

import {
  sendChatMessage,
  streamChatMessage,
  type ChatApiError,
} from "./chat-api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function sseResponse(chunks: string[]) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk));
        }
        controller.close();
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
}

describe("sendChatMessage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a valid assistant response", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ assistant: { content: "可以帮您。" }, model: "test-model" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = {
      message: "你好",
      thread_id: "thread-1",
      request_id: "request-1",
    };

    await expect(sendChatMessage(request)).resolves.toEqual({
      assistant: { content: "可以帮您。" },
      model: "test-model",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat",
      expect.objectContaining({ body: JSON.stringify(request) }),
    );
  });

  it("throws the stable error returned by the BFF", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "chat_timeout", message: "客服服务响应超时，请稍后重试。" } },
          504,
        ),
      ),
    );

    await expect(sendChatMessage({ message: "你好" })).rejects.toMatchObject({
      code: "chat_timeout",
      message: "客服服务响应超时，请稍后重试。",
      status: 504,
    });
  });

  it("normalizes a browser network failure", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")));

    await expect(sendChatMessage({ message: "你好" })).rejects.toEqual(
      expect.objectContaining<Partial<ChatApiError>>({
        code: "chat_network_error",
        status: 0,
      }),
    );
  });

  it("rejects an invalid successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ assistant: { content: null } })),
    );

    await expect(sendChatMessage({ message: "你好" })).rejects.toMatchObject({
      code: "chat_invalid_response",
      status: 200,
    });
  });
});

describe("streamChatMessage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses chunked CRLF events and reports each request phase", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      sseResponse([
        ": heartbeat\r\nevent: assistant\r\nda",
        "ta: {\"content\":\"订单已发货\",\"model\":\"test-model\"}\r",
        "\n\r\nevent: done\r\ndata: {}\r\n\r\n",
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);
    const phases: string[] = [];
    const controller = new AbortController();

    await expect(
      streamChatMessage(
        { message: "order-demo-001", thread_id: "thread-1", request_id: "request-1" },
        { signal: controller.signal, onPhaseChange: (phase) => phases.push(phase) },
      ),
    ).resolves.toEqual({
      assistant: { content: "订单已发货" },
      model: "test-model",
    });
    expect(phases).toEqual(["connecting", "processing", "finalizing"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat/stream",
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it("supports lone CR separators and ignores unknown heartbeat events", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        sseResponse([
          "event: ping\rdata: not-json\r\r",
          "event: assistant\rdata: {\"content\":\"完成\",\"model\":\"m\"}\r\r",
          "event: done\rdata: {}",
        ]),
      ),
    );

    await expect(streamChatMessage({ message: "你好" })).resolves.toEqual({
      assistant: { content: "完成" },
      model: "m",
    });
  });

  it("deduplicates a replayed identical assistant event", async () => {
    const assistant = "event: assistant\ndata: {\"content\":\"完成\",\"model\":\"m\"}\n\n";
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        sseResponse([assistant, assistant, "event: done\ndata: {}\n\n"]),
      ),
    );

    await expect(streamChatMessage({ message: "你好" })).resolves.toEqual({
      assistant: { content: "完成" },
      model: "m",
    });
  });

  it("rejects conflicting duplicate, malformed, and incomplete streams", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        sseResponse([
          "event: assistant\ndata: {\"content\":\"第一条\",\"model\":\"m\"}\n\n",
          "event: assistant\ndata: {\"content\":\"第二条\",\"model\":\"m\"}\n\n",
          "event: done\ndata: {}\n\n",
        ]),
      )
      .mockResolvedValueOnce(sseResponse(["event: assistant\ndata: {broken}\n\n"]))
      .mockResolvedValueOnce(
        sseResponse(["event: assistant\ndata: {\"content\":\"完成\",\"model\":\"m\"}\n\n"]),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(streamChatMessage({ message: "冲突" })).rejects.toMatchObject({
      code: "chat_invalid_response",
    });
    await expect(streamChatMessage({ message: "损坏" })).rejects.toMatchObject({
      code: "chat_invalid_response",
    });
    await expect(streamChatMessage({ message: "未结束" })).rejects.toMatchObject({
      code: "chat_invalid_response",
    });
  });

  it("preserves stable JSON and in-stream errors", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          { error: { code: "chat_unauthorized", message: "登录状态无效，请重新登录。" } },
          401,
        ),
      )
      .mockResolvedValueOnce(
        sseResponse([
          "event: error\ndata: {\"error\":{\"code\":\"chat_timeout\",\"message\":\"响应超时。\"}}\n\n",
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(streamChatMessage({ message: "认证" })).rejects.toMatchObject({
      code: "chat_unauthorized",
      status: 401,
    });
    await expect(streamChatMessage({ message: "超时" })).rejects.toMatchObject({
      code: "chat_timeout",
      message: "响应超时。",
    });
  });

  it("normalizes network failures, cancellation, and non-SSE responses", async () => {
    const controller = new AbortController();
    controller.abort();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockRejectedValueOnce(new DOMException("aborted", "AbortError"))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(streamChatMessage({ message: "网络" })).rejects.toMatchObject({
      code: "chat_network_error",
    });
    await expect(
      streamChatMessage({ message: "取消" }, { signal: controller.signal }),
    ).rejects.toMatchObject({ code: "chat_cancelled" });
    await expect(streamChatMessage({ message: "错误格式" })).rejects.toMatchObject({
      code: "chat_invalid_response",
    });
  });
});
