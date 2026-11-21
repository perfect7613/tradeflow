import type { NextConfig } from "next";

// Static export: FastAPI serves web/out from the same origin as the API, so one
// HTTPS tunnel (ngrok) covers the PWA, the API, WebSockets and Cashfree webhooks.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: false,
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
