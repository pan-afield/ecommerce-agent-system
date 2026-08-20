import { describe, expect, it, vi } from "vitest";

import type { LocalChatSession } from "@/types/chat";

import {
  CHAT_SESSION_STORAGE_KEY,
  clearChatSession,
  loadChatSession,
  saveChatSession,
} from "./chat-session";

const sessionFixture: LocalChatSession = {
  threadId: "thread-1",
  messages: [
    {
      id: "message-1",
      role: "user",
      content: "你好",
      requestId: "request-1",
      state: "sent",
    },
    {
      id: "message-2",
      role: "assistant",
      content: "您好",
      model: "test-model",
      state: "sent",
    },
  ],
};

describe("chat session storage", () => {
  it("saves, restores, and clears a valid session", () => {
    const storage = new Map<string, string>();
    const storageAdapter = {
      getItem: vi.fn((key: string) => storage.get(key) ?? null),
      removeItem: vi.fn((key: string) => storage.delete(key)),
      setItem: vi.fn((key: string, value: string) => storage.set(key, value)),
    } as unknown as Storage;

    saveChatSession(storageAdapter, sessionFixture);
    expect(loadChatSession(storageAdapter)).toEqual(sessionFixture);

    clearChatSession(storageAdapter);
    expect(loadChatSession(storageAdapter)).toBeNull();
  });

  it("turns an interrupted request into a retryable failure", () => {
    const storage = {
      getItem: vi.fn(() =>
        JSON.stringify({
          threadId: "thread-1",
          messages: [
            {
              id: "message-1",
              role: "user",
              content: "尚未返回",
              requestId: "request-1",
              state: "pending",
            },
          ],
        }),
      ),
    } as unknown as Storage;

    expect(loadChatSession(storage)).toEqual({
      threadId: "thread-1",
      messages: [
        expect.objectContaining({
          state: "failed",
          error: {
            code: "chat_network_error",
            message: "上次请求尚未确认完成，请重试。",
          },
        }),
      ],
    });
  });

  it.each([
    "not-json",
    JSON.stringify({ threadId: "", messages: [] }),
    JSON.stringify({ threadId: "thread-1", messages: [{ role: "user" }] }),
    JSON.stringify({
      threadId: "thread-1",
      messages: [
        { id: "m-1", role: "user", content: "你好", state: "sent" },
      ],
    }),
    JSON.stringify({
      threadId: "thread-1",
      messages: [
        { id: "m-1", role: "assistant", content: "你好", state: "pending" },
      ],
    }),
    JSON.stringify({
      threadId: "thread-1",
      messages: [
        {
          id: "m-1",
          role: "user",
          content: "你好",
          requestId: "request-1",
          state: "failed",
        },
      ],
    }),
  ])("ignores malformed persisted data", (storedValue) => {
    const storage = {
      getItem: vi.fn((key: string) =>
        key === CHAT_SESSION_STORAGE_KEY ? storedValue : null,
      ),
    } as unknown as Storage;

    expect(loadChatSession(storage)).toBeNull();
  });
});
