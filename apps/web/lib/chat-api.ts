import {
  isChatError,
  isChatResponse,
  isChatStreamAssistantEvent,
} from "@/lib/chat-contract";
import type {
  ChatErrorCode,
  ChatRequest,
  ChatResponse,
  ChatStreamAssistantEvent,
  ChatStreamPhase,
} from "@/types/chat";

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

export async function sendChatMessage(payload: ChatRequest): Promise<ChatResponse> {
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

interface ChatStreamOptions {
  signal?: AbortSignal;
  onPhaseChange?: (phase: ChatStreamPhase) => void;
}

interface ParsedSseEvent {
  event: string;
  data: string;
}

function parseSseFrame(frame: string): ParsedSseEvent | null {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n")) {
    if (!line || line.startsWith(":")) {
      continue;
    }

    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) {
      value = value.slice(1);
    }

    if (field === "event") {
      event = value;
    } else if (field === "data") {
      data.push(value);
    }
  }

  return data.length === 0 ? null : { event, data: data.join("\n") };
}

function findSseBoundary(buffer: string) {
  const match = /\r\n\r\n|\r\r|\n\n/.exec(buffer);
  return match === null ? null : { index: match.index, length: match[0].length };
}

function invalidStreamError(status = 200) {
  return new ChatApiError(
    "chat_invalid_response",
    "客服服务返回了无法识别的流式响应，请稍后重试。",
    status,
  );
}

async function readErrorResponse(response: Response): Promise<never> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw invalidStreamError(response.status);
  }

  if (isChatError(body)) {
    throw new ChatApiError(body.error.code, body.error.message, response.status);
  }

  throw invalidStreamError(response.status);
}

export async function streamChatMessage(
  payload: ChatRequest,
  options: ChatStreamOptions = {},
): Promise<ChatResponse> {
  options.onPhaseChange?.("connecting");

  let response: Response;
  try {
    response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: options.signal,
    });
  } catch {
    if (options.signal?.aborted) {
      throw new ChatApiError("chat_cancelled", "已取消本次回复。", 0);
    }

    throw new ChatApiError(
      "chat_network_error",
      "网络连接失败，请检查连接后重试。",
      0,
    );
  }

  if (!response.ok) {
    return readErrorResponse(response);
  }

  const contentType = response.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.includes("text/event-stream") || !response.body) {
    throw invalidStreamError(response.status);
  }

  options.onPhaseChange?.("processing");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let assistant: ChatStreamAssistantEvent | null = null;
  let done = false;

  function handleFrame(frame: string) {
    const parsedEvent = parseSseFrame(frame);
    if (parsedEvent === null) {
      return;
    }

    if (
      parsedEvent.event !== "assistant" &&
      parsedEvent.event !== "done" &&
      parsedEvent.event !== "error"
    ) {
      return;
    }

    let data: unknown;
    try {
      data = JSON.parse(parsedEvent.data) as unknown;
    } catch {
      throw invalidStreamError(response.status);
    }

    if (parsedEvent.event === "assistant") {
      if (!isChatStreamAssistantEvent(data)) {
        throw invalidStreamError(response.status);
      }

      if (assistant !== null) {
        if (assistant.content === data.content && assistant.model === data.model) {
          return;
        }
        throw invalidStreamError(response.status);
      }

      assistant = data;
      options.onPhaseChange?.("finalizing");
      return;
    }

    if (parsedEvent.event === "done") {
      done = true;
      return;
    }

    if (parsedEvent.event === "error") {
      if (isChatError(data)) {
        throw new ChatApiError(data.error.code, data.error.message, response.status);
      }
      throw invalidStreamError(response.status);
    }
  }

  try {
    while (!done) {
      const { done: readerDone, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !readerDone });

      let boundary = findSseBoundary(buffer);
      while (boundary !== null) {
        const frame = buffer.slice(0, boundary.index);
        buffer = buffer.slice(boundary.index + boundary.length);
        handleFrame(frame);
        boundary = findSseBoundary(buffer);
      }

      if (readerDone) {
        if (buffer.trim().length > 0) {
          handleFrame(buffer);
          buffer = "";
        }
        break;
      }
    }
  } catch (error) {
    if (options.signal?.aborted) {
      throw new ChatApiError("chat_cancelled", "已取消本次回复。", 0);
    }
    throw error;
  } finally {
    if (done) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }

  const completedAssistant = assistant as ChatStreamAssistantEvent | null;
  if (!done || completedAssistant === null) {
    throw invalidStreamError(response.status);
  }

  return {
    assistant: { content: completedAssistant.content },
    model: completedAssistant.model,
  };
}
