"use client";

import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

export default function Drawer({
  open,
  onClose,
  title,
  sub,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  sub?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[90] flex justify-end">
      <div
        className="absolute inset-0 bg-ink/30 backdrop-blur-[1px] animate-fade-in"
        onClick={onClose}
      />
      <div className="relative w-[440px] max-w-[92vw] h-full bg-white shadow-pop flex flex-col animate-slide-in">
        <div className="flex items-start justify-between gap-4 px-6 py-5 border-b border-line">
          <div>
            <h3 className="text-base font-semibold text-ink">{title}</h3>
            {sub && <p className="text-xs text-ink-muted mt-0.5">{sub}</p>}
          </div>
          <button
            onClick={onClose}
            className="text-ink-faint hover:text-ink p-1 -m-1"
          >
            <X size={18} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {children}
        </div>
        {footer && (
          <div className="px-6 py-4 border-t border-line flex items-center justify-end gap-2 bg-canvas">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
