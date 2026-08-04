import type { HTMLAttributes } from "react";

import { cn } from "./lib/cn";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center rounded-full border border-line bg-surface px-2 text-xs font-semibold text-ink-muted",
        className,
      )}
      {...props}
    />
  );
}
