"use client";

import { play, startMic, type Mic } from "./audio";
import { wsUrl } from "./api";

/**
 * VoiceSession: mic -> backend WebSocket (/ws/call/:id or /ws/interpret) -> spoken replies.
 * Half duplex: mic frames are dropped while a reply plays, so the agent never hears itself.
 */

export interface VoiceMessage {
  type: "vad" | "heard" | "say" | "note" | "end" | "error";
  [k: string]: unknown;
}

export class VoiceSession {
  private ws: WebSocket | null = null;
  private mic: Mic | null = null;
  private queue: Promise<void> = Promise.resolve();
  private speaking = false;
  muted = false;

  constructor(
    private path: string,
    private onMessage: (m: VoiceMessage) => void,
    private onState: (s: "connecting" | "listening" | "speaking" | "closed") => void,
  ) {}

  async start(): Promise<void> {
    this.onState("connecting");
    const ws = new WebSocket(wsUrl(this.path));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      ws.onerror = () => reject(new Error("could not connect"));
    });
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data as string) as VoiceMessage;
      this.onMessage(m);
      if (m.type === "say" && typeof m.audio === "string") this.enqueue(m.audio);
      if (m.type === "end") this.queue.then(() => this.stop());
    };
    ws.onclose = () => this.stop();
    this.mic = await startMic((pcm) => {
      if (!this.speaking && !this.muted && ws.readyState === WebSocket.OPEN) ws.send(pcm);
    });
    this.onState("listening");
  }

  level(): number {
    return this.mic?.level() ?? 0;
  }

  private enqueue(audio: string) {
    this.queue = this.queue.then(async () => {
      this.speaking = true;
      this.onState("speaking");
      await play(audio);
      await new Promise((r) => setTimeout(r, 250)); // let the room echo die down
      this.speaking = false;
      if (this.ws) this.onState("listening");
    });
  }

  flush(): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "flush" }));
  }

  stop(): void {
    if (!this.ws && !this.mic) return;
    try {
      if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify({ type: "stop" }));
      this.ws?.close();
    } catch {}
    this.mic?.stop();
    this.ws = null;
    this.mic = null;
    this.onState("closed");
  }
}
