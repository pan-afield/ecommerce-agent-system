import { afterEach, describe, expect, it, vi } from "vitest";

import { sendChatMessage, type ChatApiError } from "./chat-api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
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

    await expect(sendChatMessage("你好")).resolves.toEqual({
      assistant: { content: "可以帮您。" },
      model: "test-model",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/chat",
      expect.objectContaining({ body: JSON.stringify({ message: "你好" }) }),
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

    await expect(sendChatMessage("你好")).rejects.toMatchObject({
      code: "chat_timeout",
      message: "客服服务响应超时，请稍后重试。",
      status: 504,
    });
  });

  it("normalizes a browser network failure", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline")));

    await expect(sendChatMessage("你好")).rejects.toEqual(
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

    await expect(sendChatMessage("你好")).rejects.toMatchObject({
      code: "chat_invalid_response",
      status: 200,
    });
  });
});
