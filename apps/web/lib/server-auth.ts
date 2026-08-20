import { createHmac } from "node:crypto";

const DEFAULT_DEMO_USER_ID = "demo-user-li";
const TOKEN_LIFETIME_SECONDS = 5 * 60;

function encodeBase64Url(value: string) {
  return Buffer.from(value, "utf8").toString("base64url");
}

export function createAgentCoreAuthorization(now = Date.now()) {
  const secret = process.env.JWT_SECRET_KEY?.trim();
  if (!secret || secret.length < 32) {
    return null;
  }

  const userId = process.env.AGENT_CORE_DEMO_USER_ID?.trim() || DEFAULT_DEMO_USER_ID;
  const issuedAt = Math.floor(now / 1_000);
  const encodedHeader = encodeBase64Url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const encodedPayload = encodeBase64Url(
    JSON.stringify({
      sub: userId,
      iat: issuedAt,
      exp: issuedAt + TOKEN_LIFETIME_SECONDS,
    }),
  );
  const unsignedToken = `${encodedHeader}.${encodedPayload}`;
  const signature = createHmac("sha256", secret)
    .update(unsignedToken)
    .digest("base64url");

  return `Bearer ${unsignedToken}.${signature}`;
}
