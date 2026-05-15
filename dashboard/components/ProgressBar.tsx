import clsx from "clsx";

export default function ProgressBar({
  pct,
  tone = "accent",
  marker,
  className,
  height = "h-2.5",
}: {
  pct: number;
  tone?: "accent" | "good" | "warn" | "bad";
  marker?: number; // 0-100, draws a pace marker line
  className?: string;
  height?: string;
}) {
  const w = Math.max(0, Math.min(100, pct));
  return (
    <div
      className={clsx(
        "relative w-full rounded-full bg-line2 overflow-hidden",
        height,
        className
      )}
    >
      <div
        className={clsx("h-full rounded-full transition-all duration-700", {
          "bg-accent": tone === "accent",
          "bg-good": tone === "good",
          "bg-warn": tone === "warn",
          "bg-bad": tone === "bad",
        })}
        style={{ width: `${w}%` }}
      />
      {marker != null && (
        <div
          className="absolute top-0 bottom-0 w-px bg-ink/40"
          style={{ left: `${Math.max(0, Math.min(100, marker))}%` }}
          title={`Pace: ${marker.toFixed(0)}%`}
        />
      )}
    </div>
  );
}
