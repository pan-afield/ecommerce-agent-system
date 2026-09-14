"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";

import { LogOut, RotateCcw, UserCircle, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import type { AuthUser } from "@/lib/auth-client";

interface AccountAreaProps {
  error: string | null;
  isLoggingOut: boolean;
  onLogout: () => void;
  onRetry: () => void;
  user: AuthUser;
}

function AccountIdentity({ user, mobile = false }: { user: AuthUser; mobile?: boolean }) {
  const [showFullEmail, setShowFullEmail] = useState(false);

  return (
    <div className={mobile ? "space-y-3" : "min-w-0"}>
      <p className="font-mono text-[10px] uppercase text-ink-muted">Signed in as</p>
      {mobile ? (
        <p className="break-all text-sm font-medium leading-5 text-ink">{user.email}</p>
      ) : (
        <div className="mt-1 min-w-0">
          <button
            aria-expanded={showFullEmail}
            aria-label={`查看完整邮箱 ${user.email}`}
            className="block max-w-full truncate text-left text-sm font-medium text-ink underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            onClick={() => setShowFullEmail((current) => !current)}
            type="button"
          >
            {user.email}
          </button>
          {showFullEmail && <p className="mt-1 break-all text-xs leading-5 text-ink-muted">{user.email}</p>}
        </div>
      )}
      {mobile && (
        <span className="inline-flex rounded-full bg-accent-soft px-2 py-1 font-mono text-[10px] font-semibold text-accent">
          {user.role}
        </span>
      )}
    </div>
  );
}

function LogoutAction({
  error,
  isLoggingOut,
  onLogout,
  onRetry,
}: Omit<AccountAreaProps, "user">) {
  return (
    <div className="space-y-2">
      <button
        aria-label={isLoggingOut ? "正在退出登录" : error ? "重试退出登录" : "退出登录"}
        className="inline-flex h-9 items-center justify-center gap-2 rounded-md px-2.5 text-xs font-semibold text-ink-muted transition-colors hover:bg-canvas hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-50"
        disabled={isLoggingOut}
        onClick={error ? onRetry : onLogout}
        type="button"
      >
        {isLoggingOut ? (
          "正在退出…"
        ) : error ? (
          <>
            <RotateCcw className="size-3.5" aria-hidden="true" />
            重试退出
          </>
        ) : (
          <>
            <LogOut className="size-3.5" aria-hidden="true" />
            退出登录
          </>
        )}
      </button>
      {error && (
        <p className="max-w-[15rem] text-xs leading-5 text-accent" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}

export function DesktopAccountArea({ error, isLoggingOut, onLogout, onRetry, user }: AccountAreaProps) {
  return (
    <section aria-label="当前用户" className="shrink-0 rounded-md border border-line bg-surface px-3 py-3">
      <AccountIdentity user={user} />
      <div className="mt-3 flex items-center justify-between gap-2">
        <span className="rounded-full bg-accent-soft px-2 py-1 font-mono text-[10px] font-semibold text-accent">
          {user.role}
        </span>
        <LogoutAction error={error} isLoggingOut={isLoggingOut} onLogout={onLogout} onRetry={onRetry} />
      </div>
      <span className="sr-only" aria-live="polite">
        {isLoggingOut ? "正在退出登录" : ""}
      </span>
    </section>
  );
}

export function MobileAccountArea({ error, isLoggingOut, onLogout, onRetry, user }: AccountAreaProps) {
  const [isOpen, setIsOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const reduceMotion = useReducedMotion() ?? false;

  useEffect(() => {
    if (!isOpen) return;
    closeRef.current?.focus();

    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = document.getElementById("mobile-account-dialog");
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])"),
      ).filter((element) => !element.hasAttribute("disabled"));
      if (focusable.length === 0) return;
      const first = focusable[0]!;
      const last = focusable[focusable.length - 1]!;
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  function close() {
    setIsOpen(false);
    queueMicrotask(() => triggerRef.current?.focus());
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  return (
    <>
      <button
        aria-controls="mobile-account-dialog"
        aria-expanded={isOpen}
        aria-label="账户"
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-canvas hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent md:hidden"
        onClick={() => setIsOpen(true)}
        ref={triggerRef}
        type="button"
      >
        <UserCircle className="size-5" aria-hidden="true" />
      </button>

      <AnimatePresence initial={false}>
        {isOpen && (
          <motion.div
            animate={reduceMotion ? { opacity: 1 } : { opacity: 1 }}
            className="fixed inset-0 z-50 flex items-end bg-ink/25 p-3 pb-[calc(0.75rem+env(safe-area-inset-bottom))] md:hidden"
            exit={reduceMotion ? { opacity: 0 } : { opacity: 0 }}
            initial={reduceMotion ? false : { opacity: 0 }}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) close();
            }}
          >
            <motion.div
              animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0 }}
              aria-labelledby="mobile-account-title"
              aria-modal="true"
            className="w-full rounded-md border border-line bg-surface-raised p-4 shadow-shell"
            data-motion-mode={reduceMotion ? "reduced" : "standard"}
              id="mobile-account-dialog"
              initial={reduceMotion ? false : { opacity: 0, y: 16 }}
              onKeyDown={handleDialogKeyDown}
              role="dialog"
              transition={{ duration: reduceMotion ? 0 : 0.2 }}
            >
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-bold text-ink" id="mobile-account-title">账户</h2>
                <button
                  aria-label="关闭账户面板"
                  className="inline-flex size-11 items-center justify-center rounded-md text-ink-muted transition-colors hover:bg-canvas hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  onClick={close}
                  ref={closeRef}
                  title="关闭账户面板"
                  type="button"
                >
                  <X className="size-4" aria-hidden="true" />
                </button>
              </div>
              <div className="mt-4 border-t border-line pt-4">
                <AccountIdentity mobile user={user} />
              </div>
              <div className="mt-4 border-t border-line pt-3">
                <LogoutAction error={error} isLoggingOut={isLoggingOut} onLogout={onLogout} onRetry={onRetry} />
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
