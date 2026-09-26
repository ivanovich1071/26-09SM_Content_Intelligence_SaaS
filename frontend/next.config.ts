import type { NextConfig } from "next";

// Браузер ходит на тот же origin (/api/*), Next проксирует на FastAPI — без CORS и с одним доменом в проде
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default config;
