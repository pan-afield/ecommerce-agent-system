import { cookies } from "next/headers";
import { createHmac } from "node:crypto";

export const ACCESS_TOKEN_COOKIE = "ecommerce_access_token";
export const REFRESH_TOKEN_COOKIE = "ecommerce_refresh_token";
const DEFAULT_AGENT_CORE_URL = "http://localhost:8000";

export interface SessionTokens { accessToken: string; refreshToken: string }
export interface CurrentUser { id: string; email: string; role: "CUSTOMER" | "SUPPORT" | "ADMIN" }

export async function readSessionTokens(): Promise<SessionTokens | null> {
  try {
    const store = await cookies();
    const accessToken = store.get(ACCESS_TOKEN_COOKIE)?.value;
    const refreshToken = store.get(REFRESH_TOKEN_COOKIE)?.value;
    return accessToken && refreshToken ? { accessToken, refreshToken } : null;
  } catch {
    return null;
  }
}

export async function writeSessionTokens(tokens: SessionTokens) {
  const store = await cookies();
  const options = { httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax" as const, path: "/" };
  store.set(ACCESS_TOKEN_COOKIE, tokens.accessToken, options);
  store.set(REFRESH_TOKEN_COOKIE, tokens.refreshToken, options);
}

export async function clearSessionTokens() {
  const store = await cookies();
  store.delete(ACCESS_TOKEN_COOKIE);
  store.delete(REFRESH_TOKEN_COOKIE);
}

export async function refreshSession(): Promise<SessionTokens | null> {
  const current = await readSessionTokens();
  if (!current) return null;
  try {
    const response = await fetch(`${(process.env.AGENT_CORE_URL || DEFAULT_AGENT_CORE_URL).replace(/\/+$/, "")}/v1/auth/refresh`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ refresh_token: current.refreshToken }), cache: "no-store",
    });
    if (!response.ok) return null;
    const body = (await response.json()) as { access_token?: string; refresh_token?: string };
    if (!body.access_token || !body.refresh_token) return null;
    const tokens = { accessToken: body.access_token, refreshToken: body.refresh_token };
    await writeSessionTokens(tokens);
    return tokens;
  } catch { return null; }
}

export async function getSessionAgentCoreAuthorization() {
  const session = await readSessionTokens();
  if (session) return `Bearer ${session.accessToken}`;
  return process.env.NODE_ENV === "test" ? createLegacyDemoAuthorization() : null;
}

// Legacy helper retained for isolated route tests and local approval fixtures.
export function createLegacyDemoAuthorization(now = Date.now()) {
  const secret = process.env.JWT_SECRET_KEY?.trim();
  if (!secret || secret.length < 32) return null;
  const issuedAt = Math.floor(now / 1_000);
  const encode = (value: string) => Buffer.from(value, "utf8").toString("base64url");
  const header = encode(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = encode(JSON.stringify({ sub: process.env.AGENT_CORE_DEMO_USER_ID?.trim() || "demo-user-li", iat: issuedAt, exp: issuedAt + 300 }));
  const unsigned = `${header}.${payload}`;
  return `Bearer ${unsigned}.${createHmac("sha256", secret).update(unsigned).digest("base64url")}`;
}

export function createAgentCoreApproverAuthorization(now = Date.now()) {
  if (process.env.AGENT_CORE_REFUND_APPROVAL_DEMO_ENABLED !== "true") return null;
  const approverId = process.env.REFUND_APPROVER_USER_ID?.trim();
  const secret = process.env.JWT_SECRET_KEY?.trim();
  if (!approverId || !secret || secret.length < 32) return null;
  const issuedAt = Math.floor(now / 1_000);
  const encode = (value: string) => Buffer.from(value, "utf8").toString("base64url");
  const header = encode(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const payload = encode(JSON.stringify({ sub: approverId, iat: issuedAt, exp: issuedAt + 300 }));
  const unsigned = `${header}.${payload}`;
  return `Bearer ${unsigned}.${createHmac("sha256", secret).update(unsigned).digest("base64url")}`;
}

export function createAgentCoreAuthorization(now = Date.now()) {
  return createLegacyDemoAuthorization(now);
}
