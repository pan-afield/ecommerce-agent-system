import { isChatErrorCode } from "@/lib/chat-contract";
import { isOrderDetail } from "@/lib/order-contract";
import {
  CHAT_CONTEXT_ID_MAX_LENGTH,
  type LocalChatMessage,
  type LocalChatSession,
} from "@/types/chat";

export const CHAT_SESSION_STORAGE_KEY = "relay-desk-chat-session-v0.3";

const INTERRUPTED_REQUEST_MESSAGE = "上次请求尚未确认完成，请重试。";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isContextId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.trim() === value &&
    Array.from(value).length <= CHAT_CONTEXT_ID_MAX_LENGTH
  );
}

function isStoredMessage(value: unknown): value is LocalChatMessage {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    value.id.length === 0 ||
    (value.role !== "assistant" && value.role !== "user") ||
    typeof value.content !== "string" ||
    value.content.length === 0 ||
    (value.state !== "failed" && value.state !== "pending" && value.state !== "sent")
  ) {
    return false;
  }

  if (value.role === "user" && !isContextId(value.requestId)) {
    return false;
  }

  if (value.role === "assistant" && value.state !== "sent") {
    return false;
  }

  if (value.model !== undefined && typeof value.model !== "string") {
    return false;
  }

  if (value.orderId !== undefined && !isContextId(value.orderId)) {
    return false;
  }

  if (value.order !== undefined && !isOrderDetail(value.order)) {
    return false;
  }

  if (value.error !== undefined) {
    if (
      !isRecord(value.error) ||
      !isChatErrorCode(value.error.code) ||
      typeof value.error.message !== "string" ||
      value.error.message.length === 0
    ) {
      return false;
    }
  }

  if (value.state === "failed" && value.error === undefined) {
    return false;
  }

  return true;
}

function recoverInterruptedMessage(message: LocalChatMessage): LocalChatMessage {
  if (message.state !== "pending") {
    return message;
  }

  return {
    ...message,
    state: "failed",
    error: {
      code: "chat_network_error",
      message: INTERRUPTED_REQUEST_MESSAGE,
    },
  };
}

export function createChatIdentifier(prefix: "message" | "request" | "thread") {
  return `${prefix}-${globalThis.crypto.randomUUID()}`;
}

export function loadChatSession(storage: Storage): LocalChatSession | null {
  let rawSession: string | null;
  try {
    rawSession = storage.getItem(CHAT_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }

  if (rawSession === null) {
    return null;
  }

  let value: unknown;
  try {
    value = JSON.parse(rawSession) as unknown;
  } catch {
    return null;
  }

  if (
    !isRecord(value) ||
    !isContextId(value.threadId) ||
    !Array.isArray(value.messages) ||
    !value.messages.every(isStoredMessage)
  ) {
    return null;
  }

  return {
    threadId: value.threadId,
    messages: value.messages.map(recoverInterruptedMessage),
  };
}

export function saveChatSession(storage: Storage, session: LocalChatSession) {
  try {
    storage.setItem(CHAT_SESSION_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Session persistence is progressive enhancement; chat remains usable without it.
  }
}

export function clearChatSession(storage: Storage) {
  try {
    storage.removeItem(CHAT_SESSION_STORAGE_KEY);
  } catch {
    // A blocked storage API must not block starting a new in-memory session.
  }
}
