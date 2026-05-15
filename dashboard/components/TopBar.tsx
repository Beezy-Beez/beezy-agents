"use client";

import { useEffect, useState } from "react";

function useNYTime() {
  const [time, setTime] = useState("");
  useEffect(() => {
    const tick = () => {
      const s = new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York",
        weekday: "short",
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
      }).format(new Date());
      setTime(s + " ET");
    };
    tick();
    const id = setInterval(tick, 30_000);
    return () => clearInterval(id);
  }, []);
  return time;
}

export default function TopBar() {
  const time = useNYTime();
  const [refreshing, setRefreshing] = useState(false);

  // Pulse every 30s to indicate auto-refresh
  useEffect(() => {
    const id = setInterval(() => {
      setRefreshing(true);
      setTimeout(() => setRefreshing(false), 600);
    }, 30_000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="sticky top-0 z-40 bg-[#faf6ee]/90 backdrop-blur border-b border-[#e8dcc8] px-6 py-3 flex items-center justify-between">
      <div className="text-sm text-[#8b7355]">{time}</div>
      <div className="flex items-center gap-2 text-xs text-[#8b7355]">
        <span
          className={`inline-block w-2 h-2 rounded-full transition-colors ${
            refreshing ? "bg-[#d4a847]" : "bg-[#1e7e34]"
          }`}
        />
        Auto-refreshes every 30s
      </div>
    </header>
  );
}
