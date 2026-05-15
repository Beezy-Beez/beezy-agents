"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import clsx from "clsx";

const NAV = [
  { href: "/",           label: "Overview",   icon: "◉" },
  { href: "/calendar",   label: "Calendar",   icon: "▦" },
  { href: "/audiences",  label: "Audiences",  icon: "◎" },
  { href: "/analytics",  label: "Analytics",  icon: "▲" },
  { href: "/flows",      label: "Flows",      icon: "⌁" },
  { href: "/content",    label: "Content",    icon: "✦" },
  { href: "/system",     label: "System",     icon: "⚙" },
];

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside
      className="fixed left-0 top-0 h-screen w-60 flex flex-col z-50"
      style={{ background: "#1a1208" }}
    >
      {/* Logo */}
      <div className="px-5 pt-6 pb-5 border-b border-white/10">
        <div className="flex items-center gap-2">
          <span className="text-[#d4a847] text-2xl leading-none">⬡</span>
          <div>
            <div className="text-white font-bold text-[15px] leading-tight tracking-tight">
              Beezy Agents
            </div>
            <div className="text-[#d4a847] text-[10px] uppercase tracking-widest mt-0.5">
              Operations
            </div>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        {NAV.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all",
                isActive
                  ? "bg-[#8b4513] text-white"
                  : "text-[#c4b89a] hover:text-white hover:bg-white/5"
              )}
            >
              <span
                className={clsx(
                  "text-base w-5 text-center flex-shrink-0",
                  isActive ? "text-[#d4a847]" : "text-[#8b7355]"
                )}
              >
                {item.icon}
              </span>
              {item.label}
              {isActive && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-[#d4a847]" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-white/10">
        <div className="text-[#8b7355] text-[10px] leading-relaxed">
          <div className="font-semibold text-[#c4b89a] text-[11px] mb-1">
            Beezy Beez
          </div>
          <div>17-rule validator</div>
          <div>Learning loop active</div>
        </div>
      </div>
    </aside>
  );
}
