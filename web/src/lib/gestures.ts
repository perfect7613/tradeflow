/**
 * Head-gesture detector for AirPods motion (CMHeadphoneMotionManager attitude, radians).
 *
 * Feed it samples; it calls back with "nod" | "shake" | "tilt_right" | "tilt_left".
 * Pure: no DOM, no timers. Time comes from the samples, so tests can drive it.
 *
 *   nod         pitch leaves the resting pose by > NOD_AMP and comes back within NOD_MAX_MS,
 *               while yaw and roll stay small.
 *   shake       yaw swings past +SHAKE_AMP and -SHAKE_AMP (either order) within SHAKE_MAX_MS.
 *   tilt_right  roll held past TILT_AMP for TILT_HOLD_MS (sign set by rollRightSign).
 */

export type Gesture = "nod" | "shake" | "tilt_right" | "tilt_left";

export interface Sample {
  t: number; // ms
  pitch: number;
  yaw: number;
  roll: number;
}

export interface DetectorOptions {
  nodAmp?: number;
  nodMaxMs?: number;
  shakeAmp?: number;
  shakeMaxMs?: number;
  tiltAmp?: number;
  tiltHoldMs?: number;
  cooldownMs?: number;
  rollRightSign?: 1 | -1;
}

const DEFAULTS: Required<DetectorOptions> = {
  nodAmp: 0.17,
  nodMaxMs: 1100,
  shakeAmp: 0.16,
  shakeMaxMs: 1300,
  tiltAmp: 0.26,
  tiltHoldMs: 260,
  cooldownMs: 900,
  rollRightSign: 1,
};

/** Yaw wraps at ±π; keep deltas continuous. */
function wrap(a: number): number {
  while (a > Math.PI) a -= 2 * Math.PI;
  while (a < -Math.PI) a += 2 * Math.PI;
  return a;
}

export class GestureDetector {
  private o: Required<DetectorOptions>;
  private base: { pitch: number; yaw: number; roll: number } | null = null;
  private buf: Sample[] = [];
  private cooldownUntil = 0;
  private tiltSince: number | null = null;

  constructor(
    private onGesture: (g: Gesture, at: number) => void,
    opts: DetectorOptions = {},
  ) {
    this.o = { ...DEFAULTS, ...opts };
  }

  reset(): void {
    this.base = null;
    this.buf = [];
    this.tiltSince = null;
  }

  /** Current offsets from the resting pose (for drawing the head avatar). */
  offsets(): { pitch: number; yaw: number; roll: number } {
    const last = this.buf[this.buf.length - 1];
    if (!last || !this.base) return { pitch: 0, yaw: 0, roll: 0 };
    return {
      pitch: last.pitch - this.base.pitch,
      yaw: wrap(last.yaw - this.base.yaw),
      roll: last.roll - this.base.roll,
    };
  }

  push(s: Sample): Gesture | null {
    if (!this.base) this.base = { pitch: s.pitch, yaw: s.yaw, roll: s.roll };
    const d = { t: s.t, pitch: s.pitch - this.base.pitch, yaw: wrap(s.yaw - this.base.yaw), roll: s.roll - this.base.roll };

    // Slowly follow the resting pose while the head is still, so posture drift is ignored.
    const still = Math.abs(d.pitch) < 0.06 && Math.abs(d.yaw) < 0.06 && Math.abs(d.roll) < 0.06;
    if (still) {
      const a = 0.05;
      this.base.pitch += a * d.pitch;
      this.base.yaw = wrap(this.base.yaw + a * d.yaw);
      this.base.roll += a * d.roll;
    }

    this.buf.push({ ...s, pitch: d.pitch, yaw: d.yaw, roll: d.roll });
    const horizon = Math.max(this.o.nodMaxMs, this.o.shakeMaxMs) + 200;
    while (this.buf.length && s.t - this.buf[0].t > horizon) this.buf.shift();

    if (s.t < this.cooldownUntil) return null;
    const g = this.tilt(d) ?? this.shake(s.t) ?? this.nod(s.t);
    if (g) {
      this.cooldownUntil = s.t + this.o.cooldownMs;
      this.buf = [];
      this.tiltSince = null;
      this.onGesture(g, s.t);
    }
    return g;
  }

  private nod(now: number): Gesture | null {
    const w = this.buf.filter((x) => now - x.t <= this.o.nodMaxMs);
    if (w.length < 4) return null;
    let peakI = 0;
    for (let i = 1; i < w.length; i++) if (Math.abs(w[i].pitch) > Math.abs(w[peakI].pitch)) peakI = i;
    const peak = w[peakI];
    if (Math.abs(peak.pitch) < this.o.nodAmp) return null;
    const startedNearRest = Math.abs(w[0].pitch) < this.o.nodAmp * 0.45;
    const last = w[w.length - 1];
    const returned = peakI < w.length - 1 && Math.abs(last.pitch) < Math.abs(peak.pitch) * 0.4;
    const maxYaw = Math.max(...w.map((x) => Math.abs(x.yaw)));
    const maxRoll = Math.max(...w.map((x) => Math.abs(x.roll)));
    const dominant = maxYaw < Math.abs(peak.pitch) * 0.6 && maxRoll < Math.abs(peak.pitch) * 0.8;
    return startedNearRest && returned && dominant ? "nod" : null;
  }

  private shake(now: number): Gesture | null {
    const w = this.buf.filter((x) => now - x.t <= this.o.shakeMaxMs);
    if (w.length < 4) return null;
    const amp = this.o.shakeAmp;
    let sawPos = false;
    let sawNeg = false;
    let maxPitch = 0;
    let maxYaw = 0;
    for (const x of w) {
      if (x.yaw > amp) sawPos = true;
      if (x.yaw < -amp) sawNeg = true;
      maxPitch = Math.max(maxPitch, Math.abs(x.pitch));
      maxYaw = Math.max(maxYaw, Math.abs(x.yaw));
    }
    return sawPos && sawNeg && maxPitch < maxYaw * 0.7 ? "shake" : null;
  }

  private tilt(d: Sample): Gesture | null {
    const r = d.roll * this.o.rollRightSign;
    const tilted = Math.abs(r) > this.o.tiltAmp && Math.abs(d.pitch) < 0.2 && Math.abs(d.yaw) < 0.25;
    if (!tilted) {
      this.tiltSince = null;
      return null;
    }
    this.tiltSince ??= d.t;
    if (d.t - this.tiltSince >= this.o.tiltHoldMs) return r > 0 ? "tilt_right" : "tilt_left";
    return null;
  }
}
