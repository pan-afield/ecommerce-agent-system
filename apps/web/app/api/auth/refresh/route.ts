import { NextResponse } from "next/server";
import { refreshSession } from "@/lib/server-auth";
export async function POST() {
  const tokens = await refreshSession();
  return tokens ? NextResponse.json({ ok: true }) : NextResponse.json({ error: { code: "auth_refresh_invalid", message: "登录状态已失效，请重新登录。" } }, { status: 401 });
}
