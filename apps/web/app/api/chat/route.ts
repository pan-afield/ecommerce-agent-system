import { NextResponse } from "next/server";

import { getBackendDetail } from "@/lib/backend-error";
import { isBackendChatError, isChatResponse } from "@/lib/chat-contract";
import { getSessionAgentCoreAuthorization as createAgentCoreAuthorization } from "@/lib/server-auth";
import {
  CHAT_CONTEXT_ID_MAX_LENGTH,
  CHAT_MESSAGE_MAX_LENGTH,
  type ChatError,
  type ChatRequest,
} from "@/types/chat";

const DEFAULT_AGENT_CORE_URL = "http://localhost:8000";

function errorResponse(
  status: number,
  code: ChatError["error"]["code"],
  message: string,
) {
  return NextResponse.json<ChatError>({ error: { code, message } }, { status });
}

function getMessageLength(message: string) {
  return Array.from(message).length;
}

function normalizeOptionalId(value: unknown) {
  return typeof value === "string" ? value.trim() : value;
}

function isValidOptionalId(value: unknown): value is string | undefined {
  return (
    value === undefined ||
    (typeof value === "string" &&
      getMessageLength(value) > 0 &&
      getMessageLength(value) <= CHAT_CONTEXT_ID_MAX_LENGTH)
  );
}

export async function POST(request: Request) {
  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("application/json")) {
    return errorResponse(415, "chat_invalid_request", "请求必须使用 JSON 格式。");
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return errorResponse(400, "chat_invalid_request", "请求 JSON 无效。");
  }

  if (
    typeof body !== "object" ||
    body === null ||
    !("message" in body) ||
    typeof body.message !== "string"
  ) {
    return errorResponse(400, "chat_invalid_request", "message 必须是字符串。");
  }

  const message = body.message.trim();
  const messageLength = getMessageLength(message);
  if (messageLength === 0 || messageLength > CHAT_MESSAGE_MAX_LENGTH) {
    return errorResponse(
      400,
      "chat_invalid_request",
      `message 长度必须为 1 至 ${CHAT_MESSAGE_MAX_LENGTH} 个字符。`,
    );
  }

  const threadId = normalizeOptionalId("thread_id" in body ? body.thread_id : undefined);
  const requestId = normalizeOptionalId("request_id" in body ? body.request_id : undefined);

  if (!isValidOptionalId(threadId) || !isValidOptionalId(requestId)) {
    return errorResponse(
      400,
      "chat_invalid_request",
      `thread_id 和 request_id 长度必须为 1 至 ${CHAT_CONTEXT_ID_MAX_LENGTH} 个字符。`,
    );
  }

  if (requestId !== undefined && threadId === undefined) {
    return errorResponse(
      400,
      "chat_invalid_request",
      "提供 request_id 时必须同时提供 thread_id。",
    );
  }

  const payload: ChatRequest = {
    message,
    ...(threadId === undefined ? {} : { thread_id: threadId }),
    ...(requestId === undefined ? {} : { request_id: requestId }),
  };
  const authorization = await createAgentCoreAuthorization();
  if (authorization === null) {
    return errorResponse(
      503,
      "chat_auth_unavailable",
      "认证服务尚未配置，请联系管理员。",
    );
  }
  const agentCoreUrl = (process.env.AGENT_CORE_URL || DEFAULT_AGENT_CORE_URL).replace(/\/+$/, "");

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(`${agentCoreUrl}/v1/chat`, {
      method: "POST",
      headers: {
        authorization,
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
  } catch {
    return errorResponse(
      503,
      "chat_upstream_unreachable",
      "无法连接客服服务，请稍后重试。",
    );
  }

  let upstreamBody: unknown;
  try {
    upstreamBody = await upstreamResponse.json();
  } catch {
    return errorResponse(
      502,
      "chat_invalid_upstream_response",
      "客服服务返回了无效响应，请稍后重试。",
    );
  }

  if (upstreamResponse.ok && isChatResponse(upstreamBody)) {
    return NextResponse.json(upstreamBody, { status: upstreamResponse.status });
  }

  if (!upstreamResponse.ok && isBackendChatError(upstreamBody)) {
    return NextResponse.json(upstreamBody, { status: upstreamResponse.status });
  }

  const backendDetail = getBackendDetail(upstreamBody);
  if (upstreamResponse.status === 401 && backendDetail) {
    return errorResponse(401, "chat_unauthorized", "登录状态无效，请重新登录。");
  }

  if (
    upstreamResponse.status === 503 &&
    backendDetail === "认证服务尚未配置。"
  ) {
    return errorResponse(
      503,
      "chat_auth_unavailable",
      "认证服务尚未配置，请联系管理员。",
    );
  }

  return errorResponse(
    502,
    "chat_invalid_upstream_response",
    "客服服务返回了无效响应，请稍后重试。",
  );
}
