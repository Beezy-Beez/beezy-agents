import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <div className="text-5xl mb-3 text-ink-faint">⬡</div>
      <h2 className="text-xl font-semibold text-ink mb-1">Page not found</h2>
      <p className="text-ink-muted text-sm mb-6">
        That route doesn’t exist in the operations console.
      </p>
      <Link href="/" className="btn-primary">
        Back to Overview
      </Link>
    </div>
  );
}
