export default function Loading() {
  return (
    <div className="space-y-4 animate-pulse">
      <div className="skeleton h-8 w-48 rounded-lg" />
      <div className="skeleton h-48 w-full rounded-xl" />
      <div className="grid grid-cols-2 gap-4">
        <div className="skeleton h-32 rounded-xl" />
        <div className="skeleton h-32 rounded-xl" />
      </div>
      <div className="skeleton h-64 w-full rounded-xl" />
    </div>
  );
}
