import { NextResponse } from "next/server";
import { writeSessionTokens } from "@/lib/server-auth";

const backend = () => `${(process.env.AGENT_CORE_URL || "http://localhost:8000").replace(/\/+$/, "")}/v1/auth/login`;

export async function POST(request: Request) {
  let body: unknown;
  try { body = await request.json(); } catch { return NextResponse.json({ error: { code: "auth_invalid_request", message: "登录信息格式无效。" } }, { status: 400 }); }
  if (typeof body !== "object" || body === null || typeof (body as { email?: unknown }).email !== "string" || typeof (body as { password?: unknown }).password !== "string") {
    return NextResponse.json({ error: { code: "auth_invalid_request", message: "请输入邮箱和密码。" } }, { status: 400 });
  }
  try {
    const response = await fetch(backend(), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), cache: "no-store" });
    if (!response.ok) return NextResponse.json({ error: { code: response.status === 401 ? "auth_invalid_credentials" : "auth_service_error", message: response.status === 401 ? "邮箱或密码错误。" : "认证服务暂时不可用，请稍后重试。" } }, { status: response.status === 401 ? 401 : 503 });
    const result = (await response.json()) as { access_token?: string; refresh_token?: string };
    if (!result.access_token || !result.refresh_token) return NextResponse.json({ error: { code: "auth_invalid_response", message: "认证服务返回了无效响应。" } }, { status: 502 });
    await writeSessionTokens({ accessToken: result.access_token, refreshToken: result.refresh_token });
    return NextResponse.json({ ok: true });
  } catch { return NextResponse.json({ error: { code: "auth_unreachable", message: "无法连接认证服务，请稍后重试。" } }, { status: 503 }); }
}
