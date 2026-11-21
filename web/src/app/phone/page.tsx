"use client";

import { useEffect, useRef, useState } from "react";
import { api, LANGS, useQuery, useTradeFlow, type Call } from "@/lib/api";
import { ring, unlockAudio } from "@/lib/audio";
import { VoiceSession, type VoiceMessage } from "@/lib/voice";
import { TabBar, roleName } from "@/components/ui";

const ROLES = [
  { role: "forwarder", hint: "Explain the ₹18,000 and decide on part payment" },
  { role: "truck_owner", hint: "Someone asked to redirect your truck payment" },
  { role: "buyer", hint: "You owe ₹2,50,000; pay ₹1 by link on the call" },
];

interface Line {
  who: "agent" | "you" | "note";
  text: string;
}

export default function Phone() {
  const as = useQuery("as");
  const [state, setState] = useState<"waiting" | "ringing" | "connecting" | "listening" | "speaking" | "ended">("waiting");
  const [call, setCall] = useState<Call | null>(null);
  const [lines, setLines] = useState<Line[]>([]);
  const [typed, setTyped] = useState("");
  const [level, setLevel] = useState(0);
  const [tick, setTick] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const session = useRef<VoiceSession | null>(null);
  const stopRing = useRef<(() => void) | null>(null);
  const [answeredIds, setAnsweredIds] = useState<string[]>([]);

  // the snapshot refetches on every server change, so a new call shows up here within ~150 ms
  const { snap } = useTradeFlow();

  // A call for this role that we have not answered yet means the phone is ringing.
  const incoming =
    snap && as && (state === "waiting" || state === "ended")
      ? snap.calls.find((c) => snap.parties[c.party_id]?.role === as && c.status !== "done" && !answeredIds.includes(c.id))
      : undefined;
  const view = incoming ? "ringing" : state;

  useEffect(() => {
    if (view === "ringing") {
      try {
        stopRing.current = ring();
      } catch {}
      navigator.vibrate?.([400, 200, 400]);
    }
    return () => {
      stopRing.current?.();
      stopRing.current = null;
    };
  }, [view]);

  useEffect(() => {
    if (state !== "listening" && state !== "speaking") return;
    const id = setInterval(() => {
      setLevel(session.current?.level() ?? 0);
      setTick((t) => t + 1);
    }, 80);
    return () => clearInterval(id);
  }, [state]);

  async function answer() {
    const c = incoming;
    if (!c) return;
    unlockAudio();
    setCall(c);
    setAnsweredIds((ids) => [...ids, c.id]);
    setLines([]);
    setErr(null);
    const s = new VoiceSession(
      `/ws/call/${c.id}`,
      (m: VoiceMessage) => {
        if (m.type === "say") setLines((l) => [...l, { who: "agent", text: String(m.text) }]);
        if (m.type === "heard") setLines((l) => [...l, { who: "you", text: String(m.text) }]);
        if (m.type === "note") setLines((l) => [...l, { who: "note", text: String(m.text) }]);
        if (m.type === "error") setErr(String(m.message));
      },
      (st) => setState(st === "closed" ? "ended" : st),
    );
    session.current = s;
    try {
      await s.start();
    } catch (e) {
      setErr(`${(e as Error).message}. You can type replies instead.`);
      setState("listening");
    }
  }

  async function hangUp() {
    session.current?.stop();
    session.current = null;
    if (call) await api(`/api/calls/${call.id}/finish`, {}).catch(() => {});
    setState("ended");
  }

  async function sendTyped() {
    if (!call || !typed.trim()) return;
    const text = typed.trim();
    setTyped("");
    setLines((l) => [...l, { who: "you", text }]);
    const r = await api<{ say: { text: string }; ended: boolean; notes: string[] }>(`/api/calls/${call.id}/say`, { text });
    setLines((l) => [...l, ...r.notes.map((n) => ({ who: "note" as const, text: n })), { who: "agent", text: r.say.text }]);
    if (r.ended) setState("ended");
  }

  const shown = incoming ?? call;
  const party = shown && snap ? snap.parties[shown.party_id] : null;

  // ---- role chooser --------------------------------------------------
  if (!as) {
    return (
      <main className="mx-auto max-w-md px-5 pb-28">
        <header className="safe-top pt-6 pb-6">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-soft">Judge&apos;s phone</p>
          <h1 className="mt-1 font-display text-[40px] leading-none">Who are you playing?</h1>
          <p className="mt-2 text-[14px] text-ink-soft">Keep this open. It rings when TradeFlow calls you.</p>
        </header>
        <div className="space-y-3">
          {ROLES.map((r) => (
            <a key={r.role} href={`/phone?as=${r.role}`} className="slip block p-5 active:scale-[0.99]">
              <p className="font-display text-[24px] leading-tight">{roleName(r.role)}</p>
              <p className="mt-1 text-[13px] text-ink-soft">{r.hint}</p>
            </a>
          ))}
        </div>
        <TabBar />
      </main>
    );
  }

  const name = party?.name ?? roleName(as);

  return (
    <main className="night flex min-h-dvh flex-col pb-24">
      <div className="safe-top px-5 pt-4 text-center text-[12px] uppercase tracking-[0.2em] text-white/50">
        <a href="/phone">You are the {roleName(as).toLowerCase()} · change</a>
      </div>

      {view === "waiting" && (
        <div className="grid flex-1 place-items-center px-8 text-center">
          <div>
            <div className="mx-auto grid h-24 w-24 place-items-center rounded-full border border-white/15 font-display text-[40px] text-white/70">☏</div>
            <p className="mt-6 font-display text-[30px] text-white">Waiting for a call</p>
            <p className="mt-2 text-[14px] text-white/50">Keep this screen open. You&apos;ll hear it ring.</p>
          </div>
        </div>
      )}

      {view === "ringing" && (
        <div className="flex flex-1 flex-col items-center justify-between px-8 py-10 text-center">
          <div className="rise mt-10">
            <p className="text-[14px] text-white/60">TradeFlow · for Selvam Knits</p>
            <p className="mt-2 font-display text-[40px] leading-none text-white">Incoming call</p>
            <p className="mt-3 text-[14px] text-white/50">
              to {name} · in {LANGS[incoming?.language ?? "en-IN"] ?? incoming?.language} · recorded
            </p>
          </div>
          <div className="relative grid h-40 w-40 place-items-center">
            <span className="pulse-ring absolute inset-0 rounded-full bg-emerald-500/20" />
            <span className="pulse-ring absolute inset-0 rounded-full bg-emerald-500/20 [animation-delay:.7s]" />
            <button onClick={answer} className="relative h-24 w-24 rounded-full bg-emerald-500 font-display text-[34px] text-night shadow-[0_10px_50px_-10px_#10b981]">
              ☏
            </button>
          </div>
          <p className="text-[13px] text-white/50">Tap to answer</p>
        </div>
      )}

      {["connecting", "listening", "speaking", "ended"].includes(view) && (
        <div className="mx-auto flex w-full max-w-md flex-1 flex-col px-5">
          <div className="pt-6 text-center">
            <p className="font-display text-[30px] leading-none text-white">TradeFlow agent</p>
            <p className="mt-2 text-[13px] text-white/50">
              {state === "connecting" && "Connecting…"}
              {state === "speaking" && "Agent is speaking"}
              {state === "listening" && "Listening. Just talk."}
              {state === "ended" && "Call ended. Thank you."}
            </p>
            <div className="mx-auto mt-5 flex h-10 items-end justify-center gap-1">
              {Array.from({ length: 16 }).map((_, i) => {
                const h = state === "speaking" ? 10 + 26 * Math.abs(Math.sin(tick * 0.6 + i)) : state === "listening" ? 6 + level * 60 * (0.5 + 0.5 * Math.sin(i * 1.7)) : 4;
                return <span key={i} className={`w-1.5 rounded-full ${state === "speaking" ? "bg-glow" : "bg-emerald-400"}`} style={{ height: Math.min(40, h) }} />;
              })}
            </div>
          </div>

          <ol className="mt-6 flex-1 space-y-2.5 overflow-y-auto">
            {lines.map((l, i) =>
              l.who === "note" ? (
                <li key={i} className="rise rounded-xl bg-glow/10 p-3 text-[13px] text-glow">
                  {l.text.includes("http") ? (
                    <a href={l.text.slice(l.text.indexOf("http"))} target="_blank" className="underline underline-offset-4">
                      {l.text}
                    </a>
                  ) : (
                    l.text
                  )}
                </li>
              ) : (
                <li key={i} className={`rise flex ${l.who === "you" ? "justify-end" : ""}`}>
                  <span className={`max-w-[82%] rounded-2xl px-4 py-2.5 text-[15px] leading-snug ${l.who === "you" ? "bg-emerald-500/90 text-night" : "bg-white/10 text-white"}`}>{l.text}</span>
                </li>
              ),
            )}
          </ol>
          {err && <p className="mt-2 text-[13px] text-[#ff8a75]">{err}</p>}

          {state !== "ended" ? (
            <div className="sticky bottom-20 mt-4 space-y-3">
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  sendTyped();
                }}
                className="flex gap-2"
              >
                <input
                  value={typed}
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder="Or type a reply"
                  className="min-w-0 flex-1 rounded-full border border-white/15 bg-white/5 px-4 py-3 text-[15px] text-white placeholder:text-white/30"
                />
                <button type="submit" className="rounded-full bg-white/10 px-4 text-white">
                  ↑
                </button>
              </form>
              <button onClick={hangUp} className="mx-auto block h-16 w-16 rounded-full bg-[#e5484d] font-display text-[26px] text-white">
                ✕
              </button>
            </div>
          ) : (
            <button onClick={() => { setState("waiting"); setCall(null); }} className="mb-6 mt-4 rounded-2xl border border-white/15 py-3 text-white/80">
              Wait for the next call
            </button>
          )}
        </div>
      )}
      <TabBar dark />
    </main>
  );
}
