import { isChatError, isChatResponse } from "@/lib/chat-contract";
import type { ChatErrorCode, ChatRequest, ChatResponse } from "@/types/chat";

export class ChatApiError extends Error {
  readonly code: ChatErrorCode;
  readonly status: number;

  constructor(code: ChatErrorCode, message: string, status: number) {
    super(message);
    this.name = "ChatApiError";
    this.code = code;
    this.status = status;
  }
}

export async function sendChatMessage(message: string): Promise<ChatResponse> {
  const payload: ChatRequest = { message };

  let response: Response;
  try {
    response = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new ChatApiError(
      "chat_network_error",
      "网络连接失败，请检查连接后重试。",
      0,
    );
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ChatApiError(
      "chat_invalid_response",
      "客服服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  if (!response.ok) {
    if (isChatError(body)) {
      throw new ChatApiError(body.error.code, body.error.message, response.status);
    }

    throw new ChatApiError(
      "chat_invalid_response",
      "客服服务返回了无法识别的错误，请稍后重试。",
      response.status,
    );
  }

  if (!isChatResponse(body)) {
    throw new ChatApiError(
      "chat_invalid_response",
      "客服服务返回了无法识别的响应，请稍后重试。",
      response.status,
    );
  }

  return body;
}
