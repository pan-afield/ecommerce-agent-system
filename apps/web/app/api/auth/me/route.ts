import { NextResponse } from "next/server";
import { clearSessionTokens, getSessionAgentCoreAuthorization, refreshSession } from "@/lib/server-auth";
const endpoint = () => `${(process.env.AGENT_CORE_URL || "http://localhost:8000").replace(/\/+$/, "")}/v1/auth/me`;
export async function GET() {
  let authorization = await getSessionAgentCoreAuthorization();
  if (!authorization) return NextResponse.json({ error: { code: "auth_required", message: "请先登录。" } }, { status: 401 });
  let response = await fetch(endpoint(), { headers: { authorization }, cache: "no-store" });
  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (!refreshed) {
      await clearSessionTokens();
      return NextResponse.json({ error: { code: "auth_refresh_invalid", message: "登录状态已失效，请重新登录。" } }, { status: 401 });
    }
    authorization = `Bearer ${refreshed.accessToken}`;
    response = await fetch(endpoint(), { headers: { authorization }, cache: "no-store" });
    if (response.status === 401) {
      await clearSessionTokens();
      return NextResponse.json({ error: { code: "auth_refresh_invalid", message: "登录状态已失效，请重新登录。" } }, { status: 401 });
    }
  }
  if (!response.ok) return NextResponse.json({ error: { code: "auth_service_error", message: "无法读取当前身份。" } }, { status: response.status });
  return NextResponse.json(await response.json());
}
