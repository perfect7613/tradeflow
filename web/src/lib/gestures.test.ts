import { describe, expect, it } from "vitest";
import { GestureDetector, type Gesture, type Sample } from "./gestures";

const HZ = 25;

/** Build a sample stream from per-axis functions of time (ms). */
function stream(ms: number, f: (t: number) => Partial<Omit<Sample, "t">>, start = 0, base = { pitch: 0.05, yaw: 1.2, roll: -0.02 }): Sample[] {
  const out: Sample[] = [];
  for (let t = start; t <= start + ms; t += 1000 / HZ) {
    const v = f(t - start);
    out.push({ t, pitch: base.pitch + (v.pitch ?? 0), yaw: base.yaw + (v.yaw ?? 0), roll: base.roll + (v.roll ?? 0) });
  }
  return out;
}

function run(samples: Sample[]): Gesture[] {
  const got: Gesture[] = [];
  const d = new GestureDetector((g) => got.push(g));
  samples.forEach((s) => d.push(s));
  return got;
}

const rest = (ms: number, start = 0) => stream(ms, () => ({}), start);

describe("GestureDetector", () => {
  it("detects a nod (down and back up)", () => {
    const nod = stream(600, (t) => ({ pitch: -0.32 * Math.sin((Math.PI * t) / 600) }), 400);
    expect(run([...rest(400), ...nod, ...rest(400, 1020)])).toEqual(["nod"]);
  });

  it("detects a shake (left-right)", () => {
    const shake = stream(800, (t) => ({ yaw: 0.3 * Math.sin((2 * Math.PI * t) / 800) }), 400);
    expect(run([...rest(400), ...shake, ...rest(300, 1220)])).toEqual(["shake"]);
  });

  it("detects a held tilt to the right", () => {
    const tilt = stream(500, (t) => ({ roll: Math.min(0.4, t / 300) }), 400);
    expect(run([...rest(400), ...tilt])).toEqual(["tilt_right"]);
  });

  it("ignores small fidgets and slow posture drift", () => {
    const fidget = stream(2000, (t) => ({ pitch: 0.05 * Math.sin(t / 120), yaw: 0.06 * Math.sin(t / 90) }), 0);
    const drift = stream(4000, (t) => ({ pitch: (0.25 * t) / 4000 }), 2040);
    expect(run([...fidget, ...drift])).toEqual([]);
  });

  it("does not count a shake as a nod", () => {
    const shake = stream(800, (t) => ({ yaw: 0.35 * Math.sin((2 * Math.PI * t) / 800), pitch: 0.06 * Math.sin((4 * Math.PI * t) / 800) }), 400);
    expect(run([...rest(400), ...shake])).toEqual(["shake"]);
  });

  it("handles yaw wrapping at ±π", () => {
    const base = { pitch: 0, yaw: Math.PI - 0.05, roll: 0 };
    const shake = stream(800, (t) => ({ yaw: 0.3 * Math.sin((2 * Math.PI * t) / 800) }), 400, base);
    expect(run([...stream(400, () => ({}), 0, base), ...shake])).toEqual(["shake"]);
  });
});
