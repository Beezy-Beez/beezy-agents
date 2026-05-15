import clsx from "clsx";
import type { ReactNode } from "react";

export interface Column<T> {
  header: string;
  key?: keyof T;
  render?: (row: T, index: number) => ReactNode;
  className?: string;
  headerClassName?: string;
}

export default function DataTable<T>({
  columns,
  rows,
  rowClassName,
  onRowClick,
  emptyMessage = "No data yet.",
}: {
  columns: Column<T>[];
  rows: T[];
  rowClassName?: (row: T, index: number) => string;
  onRowClick?: (row: T, index: number) => void;
  emptyMessage?: string;
}) {
  return (
    <div className="overflow-x-auto -mx-1">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th
                key={i}
                className={clsx(
                  "text-left text-2xs font-semibold uppercase tracking-wider text-ink-faint px-3 py-2.5 border-b border-line whitespace-nowrap",
                  col.headerClassName
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="px-3 py-10 text-center text-ink-faint text-sm"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={i}
                onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                className={clsx(
                  "border-b border-line2 last:border-0 transition-colors",
                  onRowClick && "cursor-pointer",
                  "hover:bg-canvas",
                  rowClassName?.(row, i)
                )}
              >
                {columns.map((col, j) => (
                  <td
                    key={j}
                    className={clsx(
                      "px-3 py-2.5 align-middle text-ink-soft",
                      col.className
                    )}
                  >
                    {col.render
                      ? col.render(row, i)
                      : col.key
                      ? String(row[col.key] ?? "")
                      : null}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
