import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { Registrar } from "@/components/registrar";
import "./globals.css";

// Self-hosted (OFL) so the PWA works offline and builds without reaching Google Fonts.
const gloock = localFont({
  variable: "--font-gloock",
  src: [
    { path: "../fonts/gloock-latin-400-normal.woff2", weight: "400" },
    { path: "../fonts/gloock-latin-ext-400-normal.woff2", weight: "400" },
  ],
});
const hanken = localFont({
  variable: "--font-hanken",
  src: [
    { path: "../fonts/hanken-grotesk-latin-wght-normal.woff2", weight: "100 900" },
    { path: "../fonts/hanken-grotesk-latin-ext-wght-normal.woff2", weight: "100 900" },
  ],
});
const martian = localFont({
  variable: "--font-martian",
  src: [
    { path: "../fonts/martian-mono-latin-400-normal.woff2", weight: "400" },
    { path: "../fonts/martian-mono-latin-ext-400-normal.woff2", weight: "400" },
  ],
});
const tamil = localFont({ variable: "--font-anek-tamil", src: "../fonts/anek-tamil-tamil-wght-normal.woff2", weight: "100 800" });
const deva = localFont({ variable: "--font-anek-deva", src: "../fonts/anek-devanagari-devanagari-wght-normal.woff2", weight: "100 800" });

export const metadata: Metadata = {
  title: "TradeFlow",
  description: "A money control room for MSME exporters. Approve payments with a nod.",
  appleWebApp: { capable: true, title: "TradeFlow", statusBarStyle: "black-translucent" },
  icons: { icon: "/icon-192.png", apple: "/apple-touch-icon.png" },
};

export const viewport: Viewport = {
  themeColor: "#1d2550",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${gloock.variable} ${hanken.variable} ${martian.variable} ${tamil.variable} ${deva.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <Registrar />
        {children}
      </body>
    </html>
  );
}
