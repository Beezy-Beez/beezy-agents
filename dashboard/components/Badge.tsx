import clsx from "clsx";
import type { Tone } from "@/lib/format";

const TONE: Record<Tone, string> = {
  good: "bg-good-soft text-good-ink",
  warn: "bg-warn-soft text-warn-ink",
  bad: "bg-bad-soft text-bad-ink",
  accent: "bg-accent-soft text-accent-ink",
  muted: "bg-line2 text-ink-muted",
};

export default function Badge({
  children,
  tone = "muted",
  dot = false,
  className,
}: {
  children: React.ReactNode;
  tone?: Tone;
  dot?: boolean;
  className?: string;
}) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-2xs font-semibold whitespace-nowrap",
        TONE[tone],
        className
      )}
    >
      {dot && (
        <span
          className={clsx("w-1.5 h-1.5 rounded-full", {
            "bg-good": tone === "good",
            "bg-warn": tone === "warn",
            "bg-bad": tone === "bad",
            "bg-accent": tone === "accent",
            "bg-ink-faint": tone === "muted",
          })}
        />
      )}
      {children}
    </span>
  );
}
