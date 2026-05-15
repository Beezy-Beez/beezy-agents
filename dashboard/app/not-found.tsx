import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <div className="text-6xl mb-4">⬡</div>
      <h2
        className="text-2xl font-bold text-[#8b4513] mb-2"
        style={{ fontFamily: "var(--font-dm-serif)" }}
      >
        Page not found
      </h2>
      <p className="text-gray-400 text-sm mb-6">
        The page you&apos;re looking for doesn&apos;t exist.
      </p>
      <Link
        href="/"
        className="px-6 py-2.5 bg-[#8b4513] text-white rounded-lg text-sm font-semibold hover:bg-[#6d3410] transition-colors"
      >
        Back to Overview
      </Link>
    </div>
  );
}
