import {
  BACKEND_CHAT_ERROR_CODES,
  CLIENT_CHAT_ERROR_CODES,
  PROXY_CHAT_ERROR_CODES,
  type BackendChatErrorCode,
  type ChatError,
  type ChatErrorCode,
  type ChatResponse,
} from "@/types/chat";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function isChatResponse(value: unknown): value is ChatResponse {
  if (!isRecord(value) || !isRecord(value.assistant)) {
    return false;
  }

  return (
    typeof value.assistant.content === "string" &&
    value.assistant.content.length > 0 &&
    typeof value.model === "string" &&
    value.model.length > 0
  );
}

export function isBackendChatErrorCode(value: unknown): value is BackendChatErrorCode {
  return (
    typeof value === "string" &&
    (BACKEND_CHAT_ERROR_CODES as readonly string[]).includes(value)
  );
}

export function isChatErrorCode(value: unknown): value is ChatErrorCode {
  if (typeof value !== "string") {
    return false;
  }

  return [
    ...BACKEND_CHAT_ERROR_CODES,
    ...PROXY_CHAT_ERROR_CODES,
    ...CLIENT_CHAT_ERROR_CODES,
  ].includes(value as ChatErrorCode);
}

export function isChatError(value: unknown): value is ChatError {
  return (
    isRecord(value) &&
    isRecord(value.error) &&
    isChatErrorCode(value.error.code) &&
    typeof value.error.message === "string" &&
    value.error.message.length > 0
  );
}

export function isBackendChatError(value: unknown): value is ChatError {
  return isChatError(value) && isBackendChatErrorCode(value.error.code);
}
