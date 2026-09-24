import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit .next/standalone so the production Docker image can copy a
  // self-contained server (frontend/Dockerfile copies .next/standalone
  // and runs server.js).
  output: "standalone",
  // The repo root holds one lockfile and frontend/ another; pin the workspace
  // root so Turbopack stops warning about the ambiguity on every dev start.
  turbopack: {
    root: __dirname,
  },
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
