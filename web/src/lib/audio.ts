"use client";

/**
 * Audio out (to the AirPods) and mic in (16 kHz PCM16 frames for Saaras).
 *
 * iOS only lets a page play sound after a user tap, so call `unlockAudio()` from a click
 * handler once; after that, `play()` works from WebSocket/SSE callbacks too.
 */

let el: HTMLAudioElement | null = null;
let unlocked = false;

const SILENCE =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=";

export function unlockAudio(): void {
  if (typeof window === "undefined") return;
  el ??= new Audio();
  el.setAttribute("playsinline", "true");
  if (!unlocked) {
    el.src = SILENCE;
    el.play().catch(() => {});
    unlocked = true;
  }
}

export function isAudioUnlocked(): boolean {
  return unlocked;
}

let current: { stop: () => void } | null = null;

/** Play a WAV (base64 or URL). Resolves when playback ends or is stopped. */
export function play(src: string, opts: { onStart?: () => void } = {}): Promise<void> {
  el ??= new Audio();
  current?.stop();
  const url = src.startsWith("http") || src.startsWith("/") || src.startsWith("data:") ? src : `data:audio/wav;base64,${src}`;
  return new Promise((resolve) => {
    const a = el!;
    const done = () => {
      a.onended = a.onerror = a.onpause = null;
      current = null;
      resolve();
    };
    current = { stop: () => { a.pause(); done(); } };
    a.onended = done;
    a.onerror = done;
    a.src = url;
    a.play().then(() => opts.onStart?.()).catch(done);
  });
}

export function stopAudio(): void {
  current?.stop();
}

// ---------------------------------------------------------------------------
// Mic -> 16 kHz PCM16
// ---------------------------------------------------------------------------

const WORKLET = `
class Down extends AudioWorkletProcessor {
  constructor() { super(); this.ratio = sampleRate / 16000; this.acc = []; this.pos = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this.pos += 1;
      if (this.pos >= this.ratio) { this.pos -= this.ratio; this.acc.push(ch[i]); }
    }
    if (this.acc.length >= 1600) {
      const out = new Int16Array(this.acc.length);
      for (let i = 0; i < this.acc.length; i++) { const s = Math.max(-1, Math.min(1, this.acc[i])); out[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
      this.port.postMessage(out.buffer, [out.buffer]);
      this.acc = [];
    }
    return true;
  }
}
registerProcessor("down16k", Down);
`;

export interface Mic {
  stop: () => void;
  level: () => number;
}

/** Calls onFrame with ~100 ms of 16 kHz mono PCM16 at a time. */
export async function startMic(onFrame: (pcm: ArrayBuffer) => void): Promise<Mic> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AC();
  await ctx.resume();
  const src = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 512;
  src.connect(analyser);
  const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
  await ctx.audioWorklet.addModule(url);
  const node = new AudioWorkletNode(ctx, "down16k");
  node.port.onmessage = (e) => onFrame(e.data as ArrayBuffer);
  src.connect(node);
  const buf = new Uint8Array(analyser.fftSize);
  return {
    stop: () => {
      node.disconnect();
      src.disconnect();
      stream.getTracks().forEach((t) => t.stop());
      ctx.close();
    },
    level: () => {
      analyser.getByteTimeDomainData(buf);
      let peak = 0;
      for (const v of buf) peak = Math.max(peak, Math.abs(v - 128));
      return peak / 128;
    },
  };
}

/** Record a short clip (for spoken amendments like "only 30,000"). */
export async function recordClip(ms: number): Promise<Blob> {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const type = MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "audio/webm";
  const rec = new MediaRecorder(stream, { mimeType: type });
  const chunks: Blob[] = [];
  rec.ondataavailable = (e) => chunks.push(e.data);
  const done = new Promise<Blob>((resolve) => {
    rec.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      resolve(new Blob(chunks, { type }));
    };
  });
  rec.start();
  setTimeout(() => rec.state !== "inactive" && rec.stop(), ms);
  return done;
}

/** A soft two-tone ring, generated so we ship no audio files. */
export function ring(): () => void {
  const AC = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  const ctx = new AC();
  let stopped = false;
  const burst = () => {
    if (stopped) return;
    const t = ctx.currentTime;
    for (const [f, dt] of [[880, 0], [660, 0.18], [880, 0.5], [660, 0.68]] as const) {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + dt);
      g.gain.exponentialRampToValueAtTime(0.18, t + dt + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dt + 0.16);
      o.connect(g).connect(ctx.destination);
      o.start(t + dt);
      o.stop(t + dt + 0.18);
    }
    setTimeout(burst, 2200);
  };
  burst();
  return () => {
    stopped = true;
    ctx.close();
  };
}
