"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";
import {
  LayoutDashboard,
  CalendarDays,
  Users,
  TrendingUp,
  Workflow,
  FileText,
  Settings,
  Hexagon,
} from "lucide-react";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/calendar", label: "Calendar", icon: CalendarDays },
  { href: "/audiences", label: "Audiences", icon: Users },
  { href: "/analytics", label: "Analytics", icon: TrendingUp },
  { href: "/flows", label: "Flows", icon: Workflow },
  { href: "/content", label: "Content", icon: FileText },
  { href: "/system", label: "System", icon: Settings },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 h-screen w-60 flex flex-col z-50 bg-white border-r border-line">
      <div className="px-5 h-16 flex items-center gap-2.5 border-b border-line">
        <div className="grid place-items-center w-8 h-8 rounded-lg bg-accent text-white">
          <Hexagon size={17} strokeWidth={2.5} />
        </div>
        <div className="leading-tight">
          <div className="text-ink font-semibold text-[15px] tracking-tight">
            Beezy Beez
          </div>
          <div className="text-ink-faint text-2xs font-semibold uppercase tracking-widest">
            Operations
          </div>
        </div>
      </div>

      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active =
            href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                active
                  ? "bg-accent-soft text-accent-ink"
                  : "text-ink-muted hover:text-ink hover:bg-line2"
              )}
            >
              <Icon
                size={17}
                strokeWidth={active ? 2.4 : 2}
                className={active ? "text-accent" : "text-ink-faint"}
              />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="px-5 py-4 border-t border-line">
        <div className="text-2xs text-ink-faint leading-relaxed">
          <div className="font-semibold text-ink-muted mb-0.5">
            Live data · Klaviyo + Shopify
          </div>
          17-rule validator · learning loop
        </div>
      </div>
    </aside>
  );
}
