import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CHAT_SESSION_STORAGE_KEY } from "./chat-session";
import { REFUND_REQUEST_STORAGE_KEY, REFUND_SESSION_STORAGE_KEY } from "./refund-session";
import { getCurrentUser, login, logout } from "./auth-client";

describe("auth client", () => {
  beforeEach(() => window.sessionStorage.clear());
  afterEach(() => {
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  it("coalesces concurrent current-user requests", async () => {
    let resolveRequest: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveRequest = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const first = getCurrentUser();
    const second = getCurrentUser();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    resolveRequest?.(
      Response.json({ id: "admin", email: "admin.demo@example.com", role: "ADMIN" }),
    );
    await expect(Promise.all([first, second])).resolves.toEqual([
      { id: "admin", email: "admin.demo@example.com", role: "ADMIN" },
      { id: "admin", email: "admin.demo@example.com", role: "ADMIN" },
    ]);
  });

  it.each([
    ["login", () => login("admin.demo@example.com", "password")],
    ["logout", () => logout()],
  ])("clears user-scoped workspace state after successful %s", async (_action, run) => {
    window.sessionStorage.setItem(CHAT_SESSION_STORAGE_KEY, "chat");
    window.sessionStorage.setItem(REFUND_SESSION_STORAGE_KEY, "refund");
    window.sessionStorage.setItem(REFUND_REQUEST_STORAGE_KEY, "request");
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(Response.json({}, { status: 200 })),
    );

    await run();

    expect(window.sessionStorage.getItem(CHAT_SESSION_STORAGE_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(REFUND_SESSION_STORAGE_KEY)).toBeNull();
    expect(window.sessionStorage.getItem(REFUND_REQUEST_STORAGE_KEY)).toBeNull();
  });

  it("rejects failed logout responses so the current user is retained", async () => {
    window.sessionStorage.setItem(REFUND_SESSION_STORAGE_KEY, "refund");
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 })),
    );

    await expect(logout()).rejects.toThrow("退出登录失败，请重试。");
    expect(window.sessionStorage.getItem(REFUND_SESSION_STORAGE_KEY)).toBe("refund");
  });
});
