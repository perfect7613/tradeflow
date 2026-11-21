"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { GestureDetector, type Gesture } from "./gestures";

/**
 * Bridge to the AirPods.
 *
 * Inside the iOS shell ("TradeFlow Ears"), Swift calls:
 *   window.__tfMotion(pitch, yaw, roll, tMillis)   ~25 Hz, from CMHeadphoneMotionManager
 *   window.__tfEars(connected: boolean)            AirPods connected / disconnected (out of ears)
 *   window.__tfStem()                              stem press (MPRemoteCommandCenter)
 * In a plain browser there is no head motion, so keyboard keys N / S / T and on-screen
 * buttons stand in, and the Media Session "pause" action stands in for the stem.
 */

declare global {
  interface Window {
    __tfMotion?: (pitch: number, yaw: number, roll: number, t: number) => void;
    __tfEars?: (connected: boolean) => void;
    __tfStem?: () => void;
    webkit?: { messageHandlers?: Record<string, { postMessage: (m: unknown) => void }> };
  }
}

export type EarsEvent = Gesture | "stem";

export function isNativeShell(): boolean {
  return typeof navigator !== "undefined" && navigator.userAgent.includes("TradeFlowEars");
}

export function nativeLog(msg: string): void {
  window.webkit?.messageHandlers?.tf?.postMessage({ type: "log", msg });
}

export function useEars(onEvent: (e: EarsEvent) => void) {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [pose, setPose] = useState({ pitch: 0, yaw: 0, roll: 0 });
  const native = useSyncExternalStore(
    () => () => {},
    isNativeShell,
    () => false,
  );
  const [lastGesture, setLastGesture] = useState<{ g: EarsEvent; at: number } | null>(null);
  const cb = useRef(onEvent);
  useEffect(() => {
    cb.current = onEvent;
  });

  useEffect(() => {
    const fire = (g: EarsEvent) => {
      setLastGesture({ g, at: Date.now() });
      cb.current(g);
    };
    const det = new GestureDetector((g) => fire(g));
    let frame = 0;
    window.__tfMotion = (pitch, yaw, roll, t) => {
      det.push({ pitch, yaw, roll, t });
      if (++frame % 2 === 0) setPose(det.offsets());
    };
    window.__tfEars = (c) => {
      setConnected(c);
      det.reset();
    };
    window.__tfStem = () => fire("stem");
    window.webkit?.messageHandlers?.tf?.postMessage({ type: "ready" });

    const keys = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const map: Record<string, EarsEvent> = { n: "nod", s: "shake", t: "tilt_right", m: "stem" };
      const g = map[e.key.toLowerCase()];
      if (g) fire(g);
    };
    window.addEventListener("keydown", keys);

    // Browser fallback for the stem: pressing an AirPod stem pauses media.
    if ("mediaSession" in navigator) {
      try {
        navigator.mediaSession.setActionHandler("pause", () => fire("stem"));
      } catch {}
    }
    return () => {
      window.removeEventListener("keydown", keys);
      delete window.__tfMotion;
      delete window.__tfEars;
      delete window.__tfStem;
    };
  }, []);

  return { connected, pose, native, lastGesture, simulate: (g: EarsEvent) => {
    setLastGesture({ g, at: Date.now() });
    cb.current(g);
  } };
}
