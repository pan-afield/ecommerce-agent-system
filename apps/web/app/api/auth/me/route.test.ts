import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const {
  clearSessionTokensMock,
  getSessionAgentCoreAuthorizationMock,
  refreshSessionMock,
} = vi.hoisted(() => ({
  clearSessionTokensMock: vi.fn(),
  getSessionAgentCoreAuthorizationMock: vi.fn(),
  refreshSessionMock: vi.fn(),
}));

vi.mock("@/lib/server-auth", () => ({
  clearSessionTokens: clearSessionTokensMock,
  getSessionAgentCoreAuthorization: getSessionAgentCoreAuthorizationMock,
  refreshSession: refreshSessionMock,
}));

import { GET } from "./route";

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("GET /api/auth/me", () => {
  beforeEach(() => {
    clearSessionTokensMock.mockReset();
    getSessionAgentCoreAuthorizationMock.mockReset();
    refreshSessionMock.mockReset();
  });

  afterEach(() => {
    delete process.env.AGENT_CORE_URL;
    vi.unstubAllGlobals();
  });

  it("retries once with the rotated access token", async () => {
    getSessionAgentCoreAuthorizationMock.mockResolvedValue("Bearer expired");
    refreshSessionMock.mockResolvedValue({
      accessToken: "rotated-access",
      refreshToken: "rotated-refresh",
    });
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({ id: "demo-user-li", email: "demo@example.com", role: "CUSTOMER" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const response = await GET();

    expect(response.status).toBe(200);
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "http://localhost:8000/v1/auth/me",
      expect.objectContaining({ headers: { authorization: "Bearer rotated-access" } }),
    );
    expect(clearSessionTokensMock).not.toHaveBeenCalled();
  });

  it("clears the session when the rotated access token is still unauthorized", async () => {
    getSessionAgentCoreAuthorizationMock.mockResolvedValue("Bearer expired");
    refreshSessionMock.mockResolvedValue({
      accessToken: "invalid-rotated-access",
      refreshToken: "invalid-rotated-refresh",
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
        .mockResolvedValueOnce(jsonResponse({ detail: "still expired" }, 401)),
    );

    const response = await GET();

    expect(response.status).toBe(401);
    expect(clearSessionTokensMock).toHaveBeenCalledTimes(1);
    expect(await response.json()).toMatchObject({
      error: { code: "auth_refresh_invalid" },
    });
  });
});
