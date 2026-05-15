"use client";

import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md";
  loading?: boolean;
  icon?: ReactNode;
}

export default function Button({
  variant = "ghost",
  size = "md",
  loading = false,
  icon,
  children,
  className,
  disabled,
  ...rest
}: Props) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={clsx(
        "btn",
        variant === "primary" && "bg-accent text-white hover:bg-accent-ink",
        variant === "ghost" &&
          "bg-white border border-line text-ink-soft hover:bg-line2 hover:text-ink",
        variant === "danger" &&
          "bg-bad-soft text-bad-ink hover:bg-bad hover:text-white",
        size === "md" ? "px-3.5 py-2" : "px-2.5 py-1 text-xs rounded-md",
        className
      )}
    >
      {loading ? (
        <Loader2 size={14} className="animate-spin" />
      ) : (
        icon && <span className="flex-shrink-0">{icon}</span>
      )}
      {children}
    </button>
  );
}
