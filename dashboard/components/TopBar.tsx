"use client";

import { useEffect, useState } from "react";

export default function TopBar() {
  const [time, setTime] = useState("");
  const [pulse, setPulse] = useState(false);

  useEffect(() => {
    const tick = () =>
      setTime(
        new Intl.DateTimeFormat("en-US", {
          timeZone: "America/New_York",
          weekday: "short",
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
          hour12: true,
        }).format(new Date()) + " ET"
      );
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      setPulse(true);
      setTimeout(() => setPulse(false), 700);
    }, 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="sticky top-0 z-30 h-16 bg-canvas/85 backdrop-blur border-b border-line px-7 flex items-center justify-end gap-4">
      <span className="text-sm text-ink-muted tabular-nums">{time}</span>
      <span className="flex items-center gap-1.5 text-xs text-ink-muted">
        <span
          className={`w-1.5 h-1.5 rounded-full transition-colors ${
            pulse ? "bg-accent" : "bg-good"
          }`}
        />
        Live · 30s
      </span>
    </header>
  );
}
