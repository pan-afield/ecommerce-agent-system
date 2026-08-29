import { isRagError, isRagSearchResponse } from "@/lib/rag-contract";
import type { RagErrorCode, RagSearchResponse } from "@/types/rag";

export class RagApiError extends Error {
  readonly code: RagErrorCode;
  readonly status: number;

  constructor(code: RagErrorCode, message: string, status: number) {
    super(message);
    this.name = "RagApiError";
    this.code = code;
    this.status = status;
  }
}

export async function searchKnowledge(
  query: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<RagSearchResponse> {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) {
    throw new RagApiError("rag_invalid_request", "请输入知识库查询内容。", 400);
  }

  const searchParams = new URLSearchParams({
    query: normalizedQuery,
    limit: String(options.limit ?? 3),
  });

  let response: Response;
  try {
    response = await fetch(`/api/rag/search?${searchParams.toString()}`, {
      method: "GET",
      cache: "no-store",
      signal: options.signal,
    });
  } catch {
    throw new RagApiError("rag_network_error", "知识库连接失败，请检查连接后重试。", 0);
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new RagApiError(
      "rag_invalid_response",
      "知识库服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  if (!response.ok) {
    if (isRagError(body)) {
      throw new RagApiError(body.error.code, body.error.message, response.status);
    }
    throw new RagApiError(
      "rag_invalid_response",
      "知识库服务返回了无法识别的错误，请稍后重试。",
      response.status,
    );
  }

  if (!isRagSearchResponse(body)) {
    throw new RagApiError(
      "rag_invalid_response",
      "知识库服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  return body;
}
