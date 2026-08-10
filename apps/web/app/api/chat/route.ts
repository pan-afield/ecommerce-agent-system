import { NextResponse } from "next/server";

import { isBackendChatError, isChatResponse } from "@/lib/chat-contract";
import { CHAT_MESSAGE_MAX_LENGTH, type ChatError, type ChatRequest } from "@/types/chat";

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

  const payload: ChatRequest = { message };
  const agentCoreUrl = (process.env.AGENT_CORE_URL || DEFAULT_AGENT_CORE_URL).replace(/\/+$/, "");

  let upstreamResponse: Response;
  try {
    upstreamResponse = await fetch(`${agentCoreUrl}/v1/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
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

  return errorResponse(
    502,
    "chat_invalid_upstream_response",
    "客服服务返回了无效响应，请稍后重试。",
  );
}
