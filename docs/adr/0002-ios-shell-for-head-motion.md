# 2. A thin iOS shell for AirPods head motion

Status: accepted

Web pages cannot read AirPods head motion; only `CMHeadphoneMotionManager` can. We keep the
whole product in the Next.js PWA and ship a thin SwiftUI app ("TradeFlow Ears") that shows the
PWA in a WKWebView and forwards raw attitude (pitch, yaw, roll), connect/disconnect and stem
presses into the page.

Gesture detection lives in the web app (`web/src/lib/gestures.ts`), so it is unit-tested once and
the browser simulator (keys N/S/T) exercises the same code. The PWA works without the shell;
it falls back to on-screen approve/refuse.
