import { afterEach, describe, expect, it, vi } from "vitest";

import { RagApiError, searchKnowledge } from "./rag-api";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
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
