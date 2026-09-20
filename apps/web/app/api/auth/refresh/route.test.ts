import { beforeEach, describe, expect, it, vi } from "vitest";

const { clearSessionTokensMock, refreshSessionMock } = vi.hoisted(() => ({
  clearSessionTokensMock: vi.fn(),
  refreshSessionMock: vi.fn(),
}));

vi.mock("@/lib/server-auth", () => ({
  clearSessionTokens: clearSessionTokensMock,
  refreshSession: refreshSessionMock,
}));

import { POST } from "./route";

describe("POST /api/auth/refresh", () => {
  beforeEach(() => {
    clearSessionTokensMock.mockReset();
    refreshSessionMock.mockReset();
  });

  it("keeps the rotated HttpOnly session when refresh succeeds", async () => {
    refreshSessionMock.mockResolvedValue({
      accessToken: "new-access-token",
      refreshToken: "new-refresh-token",
    });

    const response = await POST();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(clearSessionTokensMock).not.toHaveBeenCalled();
  });

  it("clears both session cookies when refresh fails", async () => {
    refreshSessionMock.mockResolvedValue(null);

    const response = await POST();

    expect(response.status).toBe(401);
    expect(clearSessionTokensMock).toHaveBeenCalledTimes(1);
    expect(await response.json()).toMatchObject({
      error: { code: "auth_refresh_invalid" },
    });
  });
});
