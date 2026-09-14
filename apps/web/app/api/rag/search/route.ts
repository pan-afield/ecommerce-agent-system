import { NextResponse } from "next/server";

import { getBackendDetail } from "@/lib/backend-error";
import { isBackendRagError, isRagSearchResponse } from "@/lib/rag-contract";
import { getSessionAgentCoreAuthorization as createAgentCoreAuthorization } from "@/lib/server-auth";
import {
  RAG_LIMIT_MAX,
  RAG_QUERY_MAX_LENGTH,
  type RagError,
} from "@/types/rag";

const DEFAULT_AGENT_CORE_URL = "http://localhost:8000";
const UPSTREAM_TIMEOUT_MS = 8_000;

function errorResponse(status: number, code: RagError["error"]["code"], message: string) {
  return NextResponse.json<RagError>({ error: { code, message } }, { status });
}

function textLength(value: string) {
  return Array.from(value).length;
}

function createUpstreamSignal(requestSignal: AbortSignal) {
  const controller = new AbortController();
  let timedOut = false;
  const timeoutId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, UPSTREAM_TIMEOUT_MS);
  const abortFromRequest = () => controller.abort();

  if (requestSignal.aborted) {
    controller.abort();
  } else {
    requestSignal.addEventListener("abort", abortFromRequest, { once: true });
  }

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    cleanup: () => {
      clearTimeout(timeoutId);
      requestSignal.removeEventListener("abort", abortFromRequest);
    },
  };
}

export async function GET(request: Request) {
  const url = new URL(request.url);
  const query = url.searchParams.get("query")?.trim() ?? "";
  const rawLimit = url.searchParams.get("limit");
  const limit = rawLimit === null || rawLimit === "" ? 3 : Number(rawLimit);

  if (!query || textLength(query) > RAG_QUERY_MAX_LENGTH) {
    return errorResponse(
      400,
      "rag_invalid_request",
      `query 长度必须为 1 至 ${RAG_QUERY_MAX_LENGTH} 个字符。`,
    );
  }

  if (!Number.isInteger(limit) || limit < 1 || limit > RAG_LIMIT_MAX) {
    return errorResponse(
      400,
      "rag_invalid_request",
      `limit 必须为 1 至 ${RAG_LIMIT_MAX}。`,
    );
  }

  const authorization = await createAgentCoreAuthorization();
  if (authorization === null) {
    return errorResponse(
      503,
      "rag_auth_unavailable",
      "认证服务尚未配置，请联系管理员。",
    );
  }

  const agentCoreUrl = (process.env.AGENT_CORE_URL || DEFAULT_AGENT_CORE_URL).replace(/\/+$/, "");
  const upstream = createUpstreamSignal(request.signal);
  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(
      `${agentCoreUrl}/v1/rag/search?query=${encodeURIComponent(query)}&limit=${limit}`,
      {
        method: "GET",
        headers: {
          accept: "application/json",
          authorization,
        },
        cache: "no-store",
        signal: upstream.signal,
      },
    );
  } catch {
    const timedOut = upstream.didTimeout();
    upstream.cleanup();
    return errorResponse(
      timedOut ? 504 : 503,
      timedOut ? "rag_upstream_timeout" : "rag_upstream_unreachable",
      timedOut ? "知识库响应超时，请稍后重试。" : "无法连接知识库服务，请稍后重试。",
    );
  }
  upstream.cleanup();

  let body: unknown;
  try {
    body = await upstreamResponse.json();
  } catch {
    return errorResponse(
      502,
      "rag_invalid_upstream_response",
      "知识库服务返回了无效响应，请稍后重试。",
    );
  }

  if (upstreamResponse.ok && isRagSearchResponse(body)) {
    return NextResponse.json(body, { status: upstreamResponse.status });
  }

  if (!upstreamResponse.ok && isBackendRagError(body)) {
    return NextResponse.json(body, { status: upstreamResponse.status });
  }

  const backendDetail = getBackendDetail(body);
  if (upstreamResponse.status === 401 && backendDetail) {
    return errorResponse(401, "rag_unauthorized", "登录状态无效，请重新登录。");
  }

  if (upstreamResponse.status === 422) {
    return errorResponse(400, "rag_invalid_request", "知识库查询参数无效。");
  }

  return errorResponse(
    502,
    "rag_invalid_upstream_response",
    "知识库服务返回了无法识别的响应，请稍后重试。",
  );
}
