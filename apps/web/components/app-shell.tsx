import { Brand } from "@/components/brand";
import { ChatWorkspace } from "@/components/chat-workspace";
import { navigationItems } from "@/lib/navigation";

export function AppShell() {
  return (
    <div className="h-dvh p-0 sm:p-4 lg:p-6">
      <div className="mx-auto flex h-full max-w-[1440px] overflow-hidden border-line bg-surface shadow-shell sm:rounded-lg sm:border">
        <aside className="hidden w-64 shrink-0 flex-col border-r border-line bg-surface-raised p-5 md:flex">
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
          <div className="mt-auto border-t border-line pt-4">
            <p className="font-mono text-[10px] uppercase text-ink-muted">Release channel</p>
            <p className="mt-1 text-sm font-semibold text-ink">V0.1 · Single turn</p>
          </div>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex min-h-16 items-center border-b border-line bg-surface-raised px-5 md:hidden">
            <Brand />
          </div>
          <ChatWorkspace />
        </div>
      </div>
    </div>
  );
}
