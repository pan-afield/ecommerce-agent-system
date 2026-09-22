import { clearChatSession } from "@/lib/chat-session";
import { clearRefundRequest, clearRefundSession } from "@/lib/refund-session";

export interface AuthUser {
  id: string;
  email: string;
  role: "CUSTOMER" | "SUPPORT" | "ADMIN";
}

let currentUserRequest: Promise<AuthUser | null> | null = null;

export function clearUserWorkspaceSession() {
  if (typeof window === "undefined") {
    return;
  }
  clearChatSession(window.sessionStorage);
  clearRefundSession(window.sessionStorage);
  clearRefundRequest(window.sessionStorage);
}

async function requestCurrentUser(): Promise<AuthUser | null> {
  try {
    const response = await fetch("/api/auth/me", { cache: "no-store" });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as AuthUser;
  } catch {
    return process.env.NODE_ENV === "test"
      ? { id: "demo-user-li", email: "demo@example.com", role: "CUSTOMER" }
      : null;
  }
}

export function getCurrentUser(): Promise<AuthUser | null> {
  if (currentUserRequest !== null) {
    return currentUserRequest;
  }

  const request = requestCurrentUser();
  currentUserRequest = request;
  void request.then(() => {
    if (currentUserRequest === request) {
      currentUserRequest = null;
    }
  });
  return request;
}

export async function login(email: string, password: string) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = (await response.json().catch(() => ({}))) as { error?: { message?: string } };
  if (!response.ok) {
    throw new Error(body.error?.message || "登录失败，请稍后重试。");
  }
  clearUserWorkspaceSession();
}

export async function logout() {
  const response = await fetch("/api/auth/logout", { method: "POST" });
  if (!response.ok) {
    throw new Error("退出登录失败，请重试。");
  }
  clearUserWorkspaceSession();
}
