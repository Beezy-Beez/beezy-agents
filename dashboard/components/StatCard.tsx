import clsx from "clsx";

interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  valueClassName?: string;
}

export default function StatCard({ label, value, sub, valueClassName }: StatCardProps) {
  return (
    <div className="bg-white rounded-xl border border-[#e8dcc8] p-4 shadow-sm">
      <div className="text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] mb-1">
        {label}
      </div>
      <div className={clsx("text-2xl font-bold text-[#2c2417] leading-tight", valueClassName)}>
        {value}
      </div>
      {sub && (
        <div className="text-xs text-gray-400 mt-1">{sub}</div>
      )}
    </div>
  );
}
