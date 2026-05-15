import clsx from "clsx";
import type { ContentType, SlotStatus } from "@/lib/types";

const STATUS_CONFIG: Record<
  string,
  { label: string; className: string }
> = {
  dispatched: { label: "Sent", className: "bg-blue-600 text-white" },
  completed:  { label: "Sent", className: "bg-[#1e7e34] text-white" },
  failed:     { label: "Failed", className: "bg-[#c0392b] text-white" },
  blocked:    { label: "Blocked", className: "bg-[#e07b00] text-white" },
  planned:    { label: "Planned", className: "bg-gray-400 text-white" },
  pending:    { label: "Pending", className: "bg-gray-400 text-white" },
  cancelled:  { label: "Cancelled", className: "bg-gray-500 text-white" },
  skipped:    { label: "Skipped", className: "bg-gray-300 text-gray-700" },
  not_sent:   { label: "—", className: "bg-gray-200 text-gray-500" },
};

const CT_CONFIG: Record<
  string,
  { label: string; className: string }
> = {
  klaviyo_campaign: { label: "Email",          className: "bg-[#1a73e8] text-white" },
  sniper_followup:  { label: "Email (Sniper)",  className: "bg-[#1558b0] text-white" },
  hive_mind:        { label: "Hive Mind",       className: "bg-[#7b2d8b] text-white" },
  seo_blog:         { label: "SEO Blog",        className: "bg-[#1e7e34] text-white" },
  sleep_audio:      { label: "Sleep Audio",     className: "bg-[#0e7c7b] text-white" },
  sms_campaign:     { label: "SMS",             className: "bg-[#e07b00] text-white" },
  flow_experiment:  { label: "Flow Exp.",       className: "bg-gray-500 text-white" },
};

interface BadgeProps {
  status?: SlotStatus | string;
  contentType?: ContentType | string;
  className?: string;
}

export default function Badge({ status, contentType, className }: BadgeProps) {
  if (contentType) {
    const cfg = CT_CONFIG[contentType] ?? { label: contentType, className: "bg-gray-500 text-white" };
    return (
      <span
        className={clsx(
          "inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap",
          cfg.className,
          className
        )}
      >
        {cfg.label}
      </span>
    );
  }

  if (status) {
    const cfg = STATUS_CONFIG[status] ?? { label: status, className: "bg-gray-400 text-white" };
    return (
      <span
        className={clsx(
          "inline-block rounded-full px-2 py-0.5 text-[11px] font-semibold whitespace-nowrap",
          cfg.className,
          className
        )}
      >
        {cfg.label}
      </span>
    );
  }

  return null;
}
