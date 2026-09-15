import { afterEach, describe, expect, it, vi } from "vitest";

import { RagApiError, searchKnowledge } from "./rag-api";

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

const citation = {
  source_id: "shipping-policy-v1",
  chunk_id: "b".repeat(64),
  page_number: null,
  content: "配送时效以订单页面为准。",
  score: 0.12,
};

describe("searchKnowledge", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("returns a validated response and encodes query parameters", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ query: "配送时效", citations: [citation] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchKnowledge(" 配送时效 ")).resolves.toEqual({ query: "配送时效", citations: [citation] });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/rag/search?query=%E9%85%8D%E9%80%81%E6%97%B6%E6%95%88&limit=3",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("exposes stable backend errors and rejects malformed success bodies", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse({ error: { code: "rag_not_configured", message: "知识库检索服务尚未配置。" } }, 503))
        .mockResolvedValueOnce(jsonResponse({ query: "坏", citations: [{ source_id: "only" }] })),
    );

    await expect(searchKnowledge("配置")).rejects.toMatchObject({
      code: "rag_not_configured",
      status: 503,
    });
    await expect(searchKnowledge("坏")).rejects.toMatchObject({
      code: "rag_invalid_response",
    });
  });

  it("exposes the Retry-After cooldown for rate-limit responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "rag_rate_limited", message: "请求过于频繁，请稍后重试。" } },
          429,
          { "Retry-After": "9" },
        ),
      ),
    );

    await expect(searchKnowledge("退款")).rejects.toMatchObject({
      code: "rag_rate_limited",
      message: "请求过于频繁，请在 9 秒后重试。",
      retryAfterSeconds: 9,
      status: 429,
    });
  });

  it("uses a conservative cooldown when Retry-After is missing or invalid", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse(
          { error: { code: "rag_rate_limited", message: "请求过于频繁，请稍后重试。" } },
          429,
          { "Retry-After": "internal redis state" },
        ),
      ),
    );

    await expect(searchKnowledge("退款")).rejects.toMatchObject({
      code: "rag_rate_limited",
      message: "请求过于频繁，请在 60 秒后重试。",
      retryAfterSeconds: 60,
    });
  });

  it.each([
    ["rag_embedding_unavailable", "知识库向量服务暂时不可用。"],
    ["rag_database_incompatible", "知识库向量数据库配置不兼容。"],
    ["rag_forbidden", "当前用户没有知识库访问权限。"],
  ] as const)("accepts stable %s errors", async (code, message) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({ error: { code, message } }, 503),
      ),
    );

    await expect(searchKnowledge("退款")).rejects.toMatchObject({ code, status: 503, message });
  });

  it("rejects empty input and maps browser network failures", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchKnowledge("   ")).rejects.toEqual(
      new RagApiError("rag_invalid_request", "请输入知识库查询内容。", 400),
    );
    await expect(searchKnowledge("网络")).rejects.toMatchObject({ code: "rag_network_error", status: 0 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
