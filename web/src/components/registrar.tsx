"use client";

import { useEffect } from "react";

/** Registers the service worker so the PWA installs and the shell loads offline. */
export function Registrar() {
  useEffect(() => {
    if ("serviceWorker" in navigator && location.hostname !== "localhost") {
      navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" }).catch(() => {});
    }
  }, []);
  return null;
}
