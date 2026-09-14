import { afterEach, describe, expect, it, vi } from "vitest";

import { logout } from "./auth-client";

describe("logout", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rejects failed logout responses so the current user is retained", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 503 })),
    );

    await expect(logout()).rejects.toThrow("退出登录失败，请重试。");
  });
});
