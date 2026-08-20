import type { OrderDetail } from "@/types/order";

export const CHAT_MESSAGE_MAX_LENGTH = 2_000;
export const CHAT_CONTEXT_ID_MAX_LENGTH = 128;

export const BACKEND_CHAT_ERROR_CODES = [
  "chat_not_configured",
  "chat_authentication_failed",
  "chat_rate_limited",
  "chat_timeout",
  "chat_provider_unavailable",
] as const;

export const PROXY_CHAT_ERROR_CODES = [
  "chat_invalid_request",
  "chat_unauthorized",
  "chat_auth_unavailable",
  "chat_upstream_unreachable",
  "chat_invalid_upstream_response",
] as const;

export const CLIENT_CHAT_ERROR_CODES = [
  "chat_network_error",
  "chat_invalid_response",
  "chat_cancelled",
] as const;

export type BackendChatErrorCode = (typeof BACKEND_CHAT_ERROR_CODES)[number];
export type ProxyChatErrorCode = (typeof PROXY_CHAT_ERROR_CODES)[number];
export type ClientChatErrorCode = (typeof CLIENT_CHAT_ERROR_CODES)[number];
export type ChatErrorCode = BackendChatErrorCode | ProxyChatErrorCode | ClientChatErrorCode;

export interface ChatRequest {
  message: string;
  thread_id?: string;
  request_id?: string;
}

export interface ChatResponse {
  assistant: {
    content: string;
  };
  model: string;
}

export interface ChatStreamAssistantEvent {
  content: string;
  model: string;
}

export type ChatStreamPhase = "connecting" | "processing" | "finalizing";

export interface ChatErrorDetail {
  code: ChatErrorCode;
  message: string;
}

export interface ChatError {
  error: ChatErrorDetail;
}

export type ChatRole = "assistant" | "user";

export interface LocalChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  model?: string;
  orderId?: string;
  order?: OrderDetail;
  requestId?: string;
  state: "failed" | "pending" | "sent";
  error?: ChatErrorDetail;
}

export interface LocalChatSession {
  threadId: string;
  messages: LocalChatMessage[];
}
