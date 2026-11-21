import type { MetadataRoute } from "next";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "TradeFlow",
    short_name: "TradeFlow",
    description: "Money control room for MSME exporters. Approve with a nod.",
    start_url: "/ears",
    display: "standalone",
    orientation: "portrait",
    background_color: "#0d1024",
    theme_color: "#1d2550",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
