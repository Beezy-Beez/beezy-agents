import clsx from "clsx";
import type { ReactNode } from "react";

export interface Column<T> {
  header: string;
  key?: keyof T;
  render?: (row: T, index: number) => ReactNode;
  className?: string;
  headerClassName?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowClassName?: (row: T, index: number) => string;
  emptyMessage?: string;
}

export default function DataTable<T>({
  columns,
  rows,
  rowClassName,
  emptyMessage = "No data yet.",
}: DataTableProps<T>) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th
                key={i}
                className={clsx(
                  "text-left text-[11px] font-semibold uppercase tracking-widest text-[#8b7355] px-3 py-2 border-b-2 border-[#e8dcc8] whitespace-nowrap",
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
                className="px-3 py-6 text-center text-gray-400 italic text-sm"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row, i) => (
              <tr
                key={i}
                className={clsx(
                  "border-b border-[#f0ece4] hover:bg-[#fdf8f2] transition-colors last:border-0",
                  rowClassName?.(row, i)
                )}
              >
                {columns.map((col, j) => (
                  <td
                    key={j}
                    className={clsx("px-3 py-2 align-middle", col.className)}
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
