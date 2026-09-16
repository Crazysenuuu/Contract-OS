import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // Proxy API calls to the FastAPI backend. The frontend ships a relative
    // API_BASE ("/api/v1"), so in dev/e2e everything must resolve against the
    // frontend origin. In production the containers sit behind a reverse
    // proxy that performs the same routing.
    const backend =
      process.env.BACKEND_INTERNAL_URL || "http://localhost:8000";
    return [
      {
        source: "/api/v1/:path*",
        destination: `${backend}/api/v1/:path*`,
      },
      // Token-based external review endpoints live outside /api/v1.
      {
        source: "/review/:path*",
        destination: `${backend}/review/:path*`,
      },
    ];
  },
};

export default nextConfig;
