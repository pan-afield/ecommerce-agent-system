import { NextResponse } from "next/server";
import { clearSessionTokens, readSessionTokens } from "@/lib/server-auth";
export async function POST() {
  const session = await readSessionTokens();
  if (session) {
    try { await fetch(`${(process.env.AGENT_CORE_URL || "http://localhost:8000").replace(/\/+$/, "")}/v1/auth/logout`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ refresh_token: session.refreshToken }), cache: "no-store" }); } catch { /* local logout still clears cookies */ }
  }
  await clearSessionTokens();
  return new NextResponse(null, { status: 204 });
}
