"use client";

import type { PacingStatus } from "@/lib/types";

interface RevenueGaugeProps {
  pct: number;
  status: PacingStatus;
  rev: number;
  goal: number;
}

export default function RevenueGauge({ pct, status, rev, goal }: RevenueGaugeProps) {
  const r = 70;
  const circ = 2 * Math.PI * r;
  const filled = circ * Math.min(pct / 100, 1.0);
  const empty = circ - filled;

  let gaugeColor = "#1e7e34";
  if (status === "BEHIND") {
    gaugeColor = pct < (rev / goal) * 100 * 0.9 ? "#c0392b" : "#e07b00";
  }

  return (
    <div className="relative flex-shrink-0" style={{ width: 160, height: 160 }}>
      <svg width="160" height="160" viewBox="0 0 160 160">
        <circle
          cx="80" cy="80" r={r}
          fill="none"
          stroke="#e8dcc8"
          strokeWidth="14"
        />
        <circle
          cx="80" cy="80" r={r}
          fill="none"
          stroke={gaugeColor}
          strokeWidth="14"
          strokeDasharray={`${filled.toFixed(1)} ${empty.toFixed(1)}`}
          strokeLinecap="round"
          transform="rotate(-90 80 80)"
          style={{ transition: "stroke-dasharray 0.6s ease" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
        <div className="font-bold text-3xl leading-none" style={{ color: gaugeColor }}>
          {pct.toFixed(0)}%
        </div>
        <div className="text-[11px] text-gray-400 mt-1">
          of ${(goal / 1000).toFixed(0)}K
        </div>
      </div>
    </div>
  );
}
