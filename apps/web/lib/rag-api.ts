import { isRagError, isRagSearchResponse } from "@/lib/rag-contract";
import { authenticatedFetch } from "@/lib/authenticated-fetch";
import {
  DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS,
  formatRateLimitMessage,
  parseRetryAfterSeconds,
} from "@/lib/retry-after";
import type { RagErrorCode, RagSearchResponse } from "@/types/rag";

export class RagApiError extends Error {
  readonly code: RagErrorCode;
  readonly retryAfterSeconds: number | undefined;
  readonly status: number;

  constructor(
    code: RagErrorCode,
    message: string,
    status: number,
    retryAfterSeconds?: number,
  ) {
    super(message);
    this.name = "RagApiError";
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
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
    response = await authenticatedFetch(`/api/rag/search?${searchParams.toString()}`, {
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
      const retryAfterSeconds =
        body.error.code === "rag_rate_limited"
          ? (parseRetryAfterSeconds(response.headers.get("retry-after")) ??
            DEFAULT_RATE_LIMIT_RETRY_AFTER_SECONDS)
          : undefined;
      throw new RagApiError(
        body.error.code,
        retryAfterSeconds === undefined
          ? body.error.message
          : formatRateLimitMessage(retryAfterSeconds),
        response.status,
        retryAfterSeconds,
      );
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
