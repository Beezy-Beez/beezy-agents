import type { ReactNode } from "react";
import { Inbox } from "lucide-react";

export function EmptyState({
  title,
  hint,
  icon,
  action,
}: {
  title: string;
  hint?: string;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-4">
      <div className="grid place-items-center w-11 h-11 rounded-xl bg-line2 text-ink-faint mb-3">
        {icon || <Inbox size={20} />}
      </div>
      <p className="text-sm font-medium text-ink-soft">{title}</p>
      {hint && <p className="text-xs text-ink-muted mt-1 max-w-sm">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ msg }: { msg?: string }) {
  return (
    <div className="card card-pad text-center text-sm text-bad-ink bg-bad-soft border-0">
      Couldn’t load this data. {msg ? `(${msg})` : ""}
    </div>
  );
}

export function CardSkeleton({ h = "h-48" }: { h?: string }) {
  return <div className={`skeleton ${h} w-full rounded-2xl`} />;
}

export function PageSkeleton() {
  return (
    <div className="space-y-5">
      <div className="skeleton h-8 w-56 rounded-lg" />
      <div className="grid grid-cols-4 gap-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="skeleton h-28 rounded-2xl" />
        ))}
      </div>
      <div className="skeleton h-72 w-full rounded-2xl" />
    </div>
  );
}
