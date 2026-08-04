import { Badge, Button, MotionReveal } from "@ecommerce-agent-system/ui";
import { CircleDashed, Layers3 } from "lucide-react";

const workspaceFacts = [
  { label: "Environment", value: "Local" },
  { label: "Release", value: "Foundation" },
  { label: "State", value: "Ready" },
];

export function WorkspacePlaceholder() {
  return (
    <main className="min-w-0 flex-1 bg-surface" aria-labelledby="workspace-title">
      <header className="flex min-h-16 items-center justify-between border-b border-line px-5 sm:px-8">
        <div>
          <p className="font-mono text-[10px] uppercase text-ink-muted">Environment / local</p>
          <h1 id="workspace-title" className="text-base font-bold text-ink">
            Service operations
          </h1>
        </div>
        <Badge>
          <span className="mr-1.5 size-1.5 rounded-full bg-positive" aria-hidden="true" />
          Local build
        </Badge>
      </header>

      <div className="mx-auto flex min-h-[calc(100vh-8rem)] max-w-4xl items-center px-5 py-12 sm:px-8">
        <MotionReveal className="w-full">
          <section className="w-full border-y border-line py-10 sm:py-14">
            <div className="mb-8 flex size-11 items-center justify-center rounded-md border border-line-strong bg-surface-raised text-accent">
              <Layers3 className="size-5" aria-hidden="true" />
            </div>
            <p className="mb-3 font-mono text-xs uppercase text-accent">Workspace / Empty</p>
            <h2 className="max-w-2xl text-3xl font-bold leading-tight text-ink-strong sm:text-4xl">
              Nothing is active yet.
            </h2>
            <p className="mt-4 max-w-xl text-sm leading-6 text-ink-muted sm:text-base">
              No work has been assigned to this environment.
            </p>

            <dl className="mt-8 grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-3">
              {workspaceFacts.map(({ label, value }) => (
                <div className="min-h-20 bg-surface-raised p-4" key={label}>
                  <dt className="font-mono text-[10px] uppercase text-ink-muted">{label}</dt>
                  <dd className="mt-2 flex items-center gap-2 text-sm font-semibold text-ink">
                    <span className="size-1.5 rounded-full bg-positive" aria-hidden="true" />
                    {value}
                  </dd>
                </div>
              ))}
            </dl>

            <div className="mt-8 flex items-center gap-3">
              <Button disabled>
                <CircleDashed className="size-4" aria-hidden="true" />
                No actions available
              </Button>
            </div>
          </section>
        </MotionReveal>
      </div>
    </main>
  );
}
