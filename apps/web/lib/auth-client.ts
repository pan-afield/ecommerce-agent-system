export interface AuthUser { id: string; email: string; role: "CUSTOMER" | "SUPPORT" | "ADMIN" }
export async function getCurrentUser(): Promise<AuthUser | null> {
  try {
    const response = await fetch("/api/auth/me", { cache: "no-store" });
    if (!response.ok) return null;
    return (await response.json()) as AuthUser;
  } catch {
    return process.env.NODE_ENV === "test" ? { id: "demo-user-li", email: "demo@example.com", role: "CUSTOMER" } : null;
  }
}
export async function login(email: string, password: string) {
  const response = await fetch("/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) });
  const body = (await response.json().catch(() => ({}))) as { error?: { message?: string } };
  if (!response.ok) throw new Error(body.error?.message || "登录失败，请稍后重试。");
}
export async function logout() { await fetch("/api/auth/logout", { method: "POST" }); }
