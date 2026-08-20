import { createHmac } from "node:crypto";

import { afterEach, describe, expect, it } from "vitest";

import { createAgentCoreAuthorization } from "./server-auth";

describe("createAgentCoreAuthorization", () => {
  afterEach(() => {
    delete process.env.AGENT_CORE_DEMO_USER_ID;
    delete process.env.JWT_SECRET_KEY;
  });

  it("creates a short-lived HS256 token for the configured demo identity", () => {
    const secret = "test-only-jwt-secret-at-least-32-bytes";
    process.env.JWT_SECRET_KEY = secret;
    process.env.AGENT_CORE_DEMO_USER_ID = "demo-user-li";

    const authorization = createAgentCoreAuthorization(1_800_000);

    expect(authorization).toMatch(/^Bearer /);
    const token = authorization?.slice("Bearer ".length) ?? "";
    const tokenParts = token.split(".");
    expect(tokenParts).toHaveLength(3);
    const [header = "", payload = "", signature = ""] = tokenParts;
    expect(JSON.parse(Buffer.from(header, "base64url").toString("utf8"))).toEqual({
      alg: "HS256",
      typ: "JWT",
    });
    expect(JSON.parse(Buffer.from(payload, "base64url").toString("utf8"))).toEqual({
      sub: "demo-user-li",
      iat: 1_800,
      exp: 2_100,
    });
    expect(signature).toBe(
      createHmac("sha256", secret)
        .update(`${header}.${payload}`)
        .digest("base64url"),
    );
  });

  it("fails closed for a missing or short secret", () => {
    expect(createAgentCoreAuthorization()).toBeNull();

    process.env.JWT_SECRET_KEY = "too-short";
    expect(createAgentCoreAuthorization()).toBeNull();
  });
});
