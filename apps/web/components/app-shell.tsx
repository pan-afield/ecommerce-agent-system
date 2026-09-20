"use client";
import { useEffect, useState } from "react";
import { DesktopAccountArea, MobileAccountArea } from "@/components/account-area";
import { Brand } from "@/components/brand";
import { ChatWorkspace } from "@/components/chat-workspace";
import { LoginScreen } from "@/components/login-screen";
import { getCurrentUser, logout, type AuthUser } from "@/lib/auth-client";
import { navigationItems } from "@/lib/navigation";
import { AUTH_SESSION_EXPIRED_EVENT } from "@/lib/authenticated-fetch";

export function AppShell() {
  const [user, setUser] = useState<AuthUser | null | undefined>(process.env.NODE_ENV === "test" ? { id: "demo-user-li", email: "demo@example.com", role: "CUSTOMER" } : undefined);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  useEffect(() => { void getCurrentUser().then(setUser); }, []);
  useEffect(() => {
    function handleExpiredSession() {
      setLogoutError(null);
      setUser(null);
    }

    window.addEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession);
    return () => window.removeEventListener(AUTH_SESSION_EXPIRED_EVENT, handleExpiredSession);
  }, []);
  if (user === undefined) return <div className="grid min-h-dvh place-items-center text-sm text-ink-muted">正在恢复登录状态…</div>;
  if (!user) return <LoginScreen onSuccess={() => { void getCurrentUser().then(setUser); }} />;
  const handleLogout = async () => {
    if (isLoggingOut) return;
    setLogoutError(null);
    setIsLoggingOut(true);
    try {
      await logout();
      setUser(null);
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : "退出登录失败，请重试。");
    } finally {
      setIsLoggingOut(false);
    }
  };

  return (
    <div className="h-dvh p-0 sm:p-4 lg:p-6">
      <div className="mx-auto flex h-full max-w-[1440px] overflow-hidden border-line bg-surface shadow-shell sm:rounded-lg sm:border">
        <aside className="hidden w-64 shrink-0 flex-col overflow-hidden border-r border-line bg-surface-raised p-5 md:flex">
          <div className="min-h-0 flex-1 overflow-y-auto">
            <Brand />
            <nav className="mt-10" aria-label="Primary navigation">
              <p className="mb-2 px-2 font-mono text-[10px] uppercase text-ink-muted">System</p>
              <ul className="space-y-1">
                {navigationItems.map(({ active, icon: Icon, label }) => (
                  <li key={label}>
                    <span
                      className={`flex h-10 items-center gap-3 rounded-md px-3 text-sm font-semibold ${
                        active ? "bg-ink text-white" : "text-ink-muted"
                      }`}
                      aria-current={active ? "page" : undefined}
                    >
                      <Icon className="size-4" aria-hidden="true" />
                      {label}
                    </span>
                  </li>
                ))}
              </ul>
            </nav>
          </div>
          <div className="mt-auto shrink-0 space-y-4 border-t border-line pt-4">
            <DesktopAccountArea
              error={logoutError}
              isLoggingOut={isLoggingOut}
              onLogout={() => void handleLogout()}
              onRetry={() => void handleLogout()}
              user={user}
            />
            <div>
              <p className="font-mono text-[10px] uppercase text-ink-muted">Release channel</p>
              <p className="mt-1 text-xs font-medium text-ink-muted">V1.0 · Refund sandbox</p>
            </div>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-16 items-center justify-between gap-3 border-b border-line bg-surface-raised px-5 md:hidden">
            <Brand />
            <MobileAccountArea
              error={logoutError}
              isLoggingOut={isLoggingOut}
              onLogout={() => void handleLogout()}
              onRetry={() => void handleLogout()}
              user={user}
            />
          </div>
          <ChatWorkspace
            approvalDemoEnabled={false}
            userRole={user.role}
          />
        </div>
      </div>
    </div>
  );
}
