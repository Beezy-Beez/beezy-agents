"use client";

import { useState, type ReactNode } from "react";
import Button from "./Button";
import { useToast } from "./Toast";

export default function ActionButton({
  label,
  run,
  okMsg,
  confirm,
  variant = "ghost",
  size = "md",
  icon,
  onDone,
}: {
  label: string;
  run: () => Promise<unknown>;
  okMsg?: string;
  confirm?: string;
  variant?: "primary" | "ghost" | "danger";
  size?: "sm" | "md";
  icon?: ReactNode;
  onDone?: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);

  async function go() {
    if (confirm && !window.confirm(confirm)) return;
    setBusy(true);
    try {
      await run();
      toast(okMsg || `${label} — done`, "success");
      onDone?.();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      variant={variant}
      size={size}
      loading={busy}
      icon={icon}
      onClick={go}
    >
      {label}
    </Button>
  );
}
