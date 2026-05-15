import clsx from "clsx";
import type { ReactNode } from "react";
import { ArrowUpRight, ArrowDownRight } from "lucide-react";

export default function StatCard({
  label,
  value,
  sub,
  delta,
  deltaTone,
  icon,
  accent,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  delta?: string;
  deltaTone?: "good" | "bad" | "muted";
  icon?: ReactNode;
  accent?: boolean;
}) {
  return (
    <div
      className={clsx(
        "card card-pad flex flex-col gap-1.5",
        accent && "ring-1 ring-accent-ring"
      )}
    >
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        {icon && <span className="text-ink-faint">{icon}</span>}
      </div>
      <div className="text-[1.7rem] leading-none stat-num">{value}</div>
      <div className="flex items-center gap-2 min-h-[1.1rem]">
        {delta && (
          <span
            className={clsx(
              "inline-flex items-center gap-0.5 text-xs font-semibold",
              deltaTone === "good" && "text-good",
              deltaTone === "bad" && "text-bad",
              (!deltaTone || deltaTone === "muted") && "text-ink-muted"
            )}
          >
            {deltaTone === "good" && <ArrowUpRight size={13} />}
            {deltaTone === "bad" && <ArrowDownRight size={13} />}
            {delta}
          </span>
        )}
        {sub && <span className="text-xs text-ink-muted">{sub}</span>}
      </div>
    </div>
  );
}
