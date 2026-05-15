/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static export — served by the Replit FastAPI server at /dashboard
  // (single-server architecture; no separate Vercel deploy required).
  output: "export",
  trailingSlash: true,
  basePath: "/dashboard",
  images: { unoptimized: true },
};

module.exports = nextConfig;
