"use client";

import { useEffect, useRef, useState } from "react";
import { LANGS } from "@/lib/api";
import { unlockAudio } from "@/lib/audio";
import { VoiceSession, type VoiceMessage } from "@/lib/voice";
import { TabBar } from "@/components/ui";

interface Bubble {
  side: "me" | "them";
  original: string;
  translated: string;
}

/**
 * Live translation. Selvam keeps one AirPod in and the phone on the table.
 * Whoever speaks is detected automatically: his Tamil becomes their language on screen
 * (and aloud, if he wants); their words come to him in Tamil, in his ear.
 */
export default function Translate() {
  const [me, setMe] = useState("ta-IN");
  const [them, setThem] = useState("hi-IN");
  const [aloud, setAloud] = useState(false);
  const [state, setState] = useState<"idle" | "connecting" | "listening" | "speaking" | "closed">("idle");
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [hearing, setHearing] = useState(false);
  const [level, setLevel] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const session = useRef<VoiceSession | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [bubbles]);
  useEffect(() => {
    if (state !== "listening") return;
    const id = setInterval(() => setLevel(session.current?.level() ?? 0), 80);
    return () => clearInterval(id);
  }, [state]);
  useEffect(() => () => session.current?.stop(), []);

  async function start() {
    unlockAudio();
    setErr(null);
    const s = new VoiceSession(
      `/ws/interpret?owner=${me}&partner=${them}&speak_partner=${aloud}`,
      (m: VoiceMessage) => {
        if (m.type === "vad") setHearing(m.signal === "START_SPEECH");
        if (m.type === "say") {
          setHearing(false);
          setBubbles((b) => [...b, { side: m.from === me ? "me" : "them", original: String(m.source_text), translated: String(m.text) }]);
        }
        if (m.type === "error") setErr(String(m.message));
      },
      (st) => setState(st === "closed" ? "idle" : st),
    );
    session.current = s;
    try {
      await s.start();
    } catch (e) {
      setErr((e as Error).message);
      setState("idle");
    }
  }

  function stop() {
    session.current?.stop();
    session.current = null;
    setState("idle");
  }

  const live = state !== "idle" && state !== "closed";
  const last = [...bubbles].reverse().find((b) => b.side === "me");

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col px-5 pb-28">
      <header className="safe-top pt-5">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-soft">Live translate</p>
        <h1 className="mt-1 font-display text-[38px] leading-none">Talk to anyone.</h1>
        <div className="mt-4 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
          <LangPick label="You hear" value={me} onChange={setMe} disabled={live} />
          <button
            disabled={live}
            onClick={() => {
              setMe(them);
              setThem(me);
            }}
            className="mt-4 grid h-9 w-9 place-items-center rounded-full border border-rule bg-card text-ink-soft disabled:opacity-40"
            aria-label="swap"
          >
            ⇄
          </button>
          <LangPick label="They speak" value={them} onChange={setThem} disabled={live} />
        </div>
        <label className="mt-3 flex items-center gap-2 text-[13px] text-ink-soft">
          <input type="checkbox" checked={aloud} disabled={live} onChange={(e) => setAloud(e.target.checked)} className="accent-ink" />
          Also speak my words aloud for them
        </label>
      </header>

      {/* show-to-them card: the latest thing Selvam said, big, in their language */}
      {last && (
        <div className="slip rise mt-5 p-5">
          <p className="text-[11px] uppercase tracking-[0.18em] text-ink-soft">For them · {LANGS[them]}</p>
          <p className="mt-2 text-[24px] leading-snug">{last.translated}</p>
        </div>
      )}

      <div className="mt-5 flex-1 space-y-3">
        {bubbles.length === 0 && (
          <div className="mt-10 text-center text-[14px] leading-relaxed text-ink-soft">
            Tap the mic and just talk, one at a time.
            <br />
            TradeFlow works out who&apos;s speaking.
            <br />
            You hear them in {LANGS[me]?.split(" ")[0]} in your AirPods.
          </div>
        )}
        {bubbles.map((b, i) => (
          <div key={i} className={`rise flex ${b.side === "me" ? "justify-end" : ""}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-3 ${b.side === "me" ? "bg-ink text-paper" : "slip"}`}>
              <p className="text-[16px] leading-snug">{b.side === "me" ? b.original : b.translated}</p>
              <p className={`mt-1 text-[13px] leading-snug ${b.side === "me" ? "text-paper/60" : "text-ink-soft"}`}>{b.side === "me" ? b.translated : b.original}</p>
            </div>
          </div>
        ))}
        {hearing && <p className="text-center text-[13px] text-ink-soft">hearing…</p>}
        <div ref={end} />
      </div>

      {err && <p className="mb-2 rounded-lg bg-stamp-red/10 p-3 text-[13px] text-stamp-red">{err}</p>}

      <div className="sticky bottom-24 mt-4 flex justify-center">
        <button
          onClick={live ? stop : start}
          className={`relative grid h-20 w-20 place-items-center rounded-full text-[28px] shadow-xl transition ${live ? "bg-stamp-red text-white" : "bg-ink text-paper"}`}
          aria-label={live ? "stop" : "start"}
        >
          {live && <span className="absolute inset-0 rounded-full bg-stamp-red/30" style={{ transform: `scale(${1 + level * 0.8})`, transition: "transform 80ms" }} />}
          <span className="relative">{live ? "■" : "🎙"}</span>
        </button>
      </div>
      <p className="mt-2 text-center text-[12px] text-ink-soft">
        {state === "connecting" ? "connecting…" : state === "speaking" ? "speaking translation…" : live ? "listening" : "tap to start"}
      </p>
      <TabBar />
    </main>
  );
}

function LangPick({ label, value, onChange, disabled }: { label: string; value: string; onChange: (v: string) => void; disabled: boolean }) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-[0.15em] text-ink-soft">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full rounded-xl border border-rule bg-card px-3 py-2.5 text-[15px] disabled:opacity-60"
      >
        {Object.entries(LANGS).map(([k, v]) => (
          <option key={k} value={k}>
            {v}
          </option>
        ))}
      </select>
    </label>
  );
}
