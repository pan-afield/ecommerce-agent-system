import type { ButtonHTMLAttributes } from "react";

import { cn } from "./lib/cn";

type ButtonVariant = "primary" | "secondary" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
}

const variantStyles: Record<ButtonVariant, string> = {
  primary:
    "bg-ink text-white shadow-[0_1px_0_rgba(255,255,255,0.16)_inset] hover:bg-ink-strong",
  secondary:
    "border border-line-strong bg-surface text-ink hover:border-ink-muted hover:bg-canvas",
  ghost: "text-ink-muted hover:bg-canvas hover:text-ink",
};

export function Button({
  className,
  type = "button",
  variant = "primary",
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md px-3 text-sm font-semibold transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-50",
        variantStyles[variant],
        className,
      )}
      type={type}
      {...props}
    />
  );
}
