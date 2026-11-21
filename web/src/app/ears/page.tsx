"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, audioUrl, useTradeFlow, type Approval, type LiveEvent } from "@/lib/api";
import { play, recordClip, stopAudio, unlockAudio } from "@/lib/audio";
import { useEars, type EarsEvent } from "@/lib/ears";
import { Dot, Stamp, TabBar, inr } from "@/components/ui";

type Phase = "idle" | "briefing" | "armed" | "explaining" | "listening" | "sending" | "done" | "expired";

export default function Ears() {
  const [started, setStarted] = useState(false);
  const [rawPhase, setPhase] = useState<Phase>("idle");
  const [armedAt, setArmedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [notice, setNotice] = useState<{ text: string; tone: "ok" | "bad" | "info" } | null>(null);
  const [said, setSaid] = useState<{ text: string; tone?: string } | null>(null);
  const [pin, setPin] = useState("");
  const [result, setResult] = useState<"approved" | "refused" | null>(null);
  const [lastPayoutId, setLastPayoutId] = useState<string | null>(null);
  const briefed = useRef<string | null>(null);
  const startedRef = useRef(false);
  const busy = useRef(false);

  const onEvent = useCallback((e: LiveEvent) => {
    if (e.event === "say" && startedRef.current) {
      setSaid({ text: e.data.text, tone: e.data.tone });
      const url = audioUrl(e.data.audio_id);
      if (url) play(url);
    }
  }, []);
  const { snap } = useTradeFlow(onEvent);
  const approval: Approval | undefined = snap?.approvals[0];
  const windowS = snap?.mode.approval_window_s ?? 10;
  const payout = approval ? snap?.payouts.find((p) => p.id === approval.payout_id) : undefined;
  const party = payout ? snap?.parties[payout.party_id] : undefined;
  const lastPayout = lastPayoutId ? snap?.payouts.find((p) => p.id === lastPayoutId) : undefined;
  const lastParty = lastPayout ? snap?.parties[lastPayout.party_id] : undefined;

  const ears = useEars((g) => handle(g));
  const earsOk = !ears.native || ears.connected !== false;

  // ---- brief playback -------------------------------------------------
  const brief = useCallback(async (a: Approval) => {
    briefed.current = a.id;
    setResult(null);
    setNotice(null);
    setPin("");
    setPhase("briefing");
    const url = audioUrl(a.audio_id);
    if (url) await play(url);
    else await new Promise((r) => setTimeout(r, 1500));
    try {
      await api(`/api/approvals/${a.id}/arm`, {});
      setArmedAt(Date.now());
      setPhase("armed");
    } catch (e) {
      setNotice({ text: (e as Error).message, tone: "bad" });
      setPhase("idle");
    }
  }, []);

  // A new approval arrived: start its brief (audio is an external system, so this is an effect).
  useEffect(() => {
    if (!started || !approval || briefed.current === approval.id) return;
    const t = setTimeout(() => brief(approval), 0);
    return () => clearTimeout(t);
  }, [started, approval, brief]);

  // countdown
  useEffect(() => {
    if (rawPhase !== "armed") return;
    const id = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(id);
  }, [rawPhase]);
  const left = armedAt ? Math.max(0, windowS - (now - armedAt) / 1000) : windowS;
  const phase: Phase =
    rawPhase === "armed" && left <= 0 ? "expired" : !approval && rawPhase !== "done" && rawPhase !== "sending" ? "idle" : rawPhase;

  // ---- gestures -------------------------------------------------------
  async function respond(gesture: "nod" | "shake" | "approve" | "refuse", source: string) {
    if (!approval || busy.current) return;
    busy.current = true;
    setPhase("sending");
    try {
      await api(`/api/approvals/${approval.id}/respond`, {
        token: approval.token,
        gesture,
        source,
        ears_in: ears.native ? ears.connected !== false : true,
        pin: pin || undefined,
      });
      const ok = gesture === "nod" || gesture === "approve";
      setLastPayoutId(approval.payout_id);
      setResult(ok ? "approved" : "refused");
      setPhase("done");
    } catch (e) {
      setNotice({ text: (e as Error).message, tone: "bad" });
      setPhase(left > 0 ? "armed" : "expired");
    } finally {
      busy.current = false;
    }
  }

  async function explain() {
    if (!approval) return;
    setPhase("explaining");
    try {
      const r = await api<{ text: string; audio_id: string | null }>(`/api/approvals/${approval.id}/explain`, {});
      setSaid({ text: r.text });
      const url = audioUrl(r.audio_id);
      if (url) await play(url);
      await api(`/api/approvals/${approval.id}/arm`, {});
      setArmedAt(Date.now());
      setPhase("armed");
    } catch (e) {
      setNotice({ text: (e as Error).message, tone: "bad" });
      setPhase("armed");
    }
  }

  async function amend() {
    if (!approval) return;
    setPhase("listening");
    setNotice({ text: "Say the new amount…", tone: "info" });
    try {
      const clip = await recordClip(3500);
      setPhase("sending");
      const fd = new FormData();
      fd.append("file", clip, clip.type.includes("mp4") ? "amend.m4a" : "amend.webm");
      const r = await api<{ heard: string; amount: number }>(`/api/approvals/${approval.id}/amend`, fd);
      setNotice({ text: `Heard “${r.heard}” → ${inr(r.amount)}. New brief coming.`, tone: "ok" });
      briefed.current = null;
    } catch (e) {
      setNotice({ text: (e as Error).message, tone: "bad" });
      setPhase("armed");
    }
  }

  function handle(g: EarsEvent) {
    if (!approval) return;
    const source = ears.native ? "airpods" : "simulator";
    if (g === "stem") {
      if (phase === "armed") amend();
      return;
    }
    if (phase === "expired" && (g === "nod" || g === "shake")) {
      setNotice({ text: "Window passed. Replay the brief first.", tone: "bad" });
      return;
    }
    if (phase !== "armed") return;
    if (!earsOk) {
      setNotice({ text: "AirPod out. Nothing can be approved.", tone: "bad" });
      return;
    }
    if (g === "nod") respond("nod", source);
    else if (g === "shake") respond("shake", source);
    else if (g === "tilt_right") explain();
  }

  // ---- render -----------------------------------------------------------
  if (!started) {
    return (
      <main className="night grid min-h-dvh place-items-center px-6 pb-24 text-center">
        <div className="rise max-w-sm">
          <Head pose={{ pitch: 0, yaw: 0, roll: 0 }} lit={false} />
          <h1 className="mt-6 font-display text-[40px] leading-[1.02] text-white">Approve with a nod.</h1>
          <p className="mt-3 text-[15px] leading-relaxed text-white/60">
            Put in your AirPods. You&apos;ll only hear payments that need you, in {snap?.mode.owner_language === "ta-IN" ? "Tamil" : "your language"}.
          </p>
          <button
            onClick={() => {
              unlockAudio();
              startedRef.current = true;
              setStarted(true);
            }}
            className="mt-8 w-full rounded-2xl bg-glow py-4 text-[17px] font-bold text-night shadow-[0_10px_40px_-10px_#f4b740]"
          >
            I&apos;m wearing them, start
          </button>
          {!ears.native && (
            <p className="mt-4 text-[12px] text-white/40">
              In a browser there&apos;s no head tracking. Use the buttons, or keys N / S / T. Open in the TradeFlow Ears app for real gestures.
            </p>
          )}
        </div>
        <TabBar dark />
      </main>
    );
  }

  const status = (() => {
    if (!approval && phase !== "done") return { big: "All quiet", small: "TradeFlow is handling the routine ones. You'll hear the rest." };
    switch (phase) {
      case "briefing":
        return { big: "Listen…", small: "The brief is playing in your AirPods" };
      case "armed":
        return { big: "Nod to approve", small: "Shake to refuse · tilt right to hear why · squeeze stem to change amount" };
      case "explaining":
        return { big: "Explaining…", small: "Three factors, most important first" };
      case "listening":
        return { big: "Say the amount", small: "For example: sirf tees hazaar" };
      case "sending":
        return { big: "One moment…", small: "" };
      case "expired":
        return { big: "Window passed", small: "Nothing was approved. Replay to hear it again." };
      case "done":
        if (result === "refused") return { big: "Refused", small: "No money moved." };
        if (lastPayout?.status === "paid") return { big: "Paid", small: "Cashfree confirmed the transfer." };
        if (lastPayout?.status === "failed") return { big: "Payment failed", small: "Cashfree couldn't complete it. Nothing was lost." };
        return { big: "Approved", small: "Sending to Cashfree… the stamp turns green when Cashfree confirms." };
      default:
        return { big: "Ready", small: "" };
    }
  })();

  const step = phase === "briefing" || phase === "explaining" ? 0 : phase === "armed" || phase === "listening" || phase === "expired" ? 1 : phase === "sending" || phase === "done" ? 2 : -1;
  const lit = phase === "armed";
  const R = 128;
  const C = 2 * Math.PI * R;

  return (
    <main className="night min-h-dvh pb-28">
      <div className="safe-top mx-auto flex max-w-md items-center justify-between px-5 pt-4 text-white/60">
        <Link href="/" className="text-[12px] uppercase tracking-[0.2em]">
          ← Room
        </Link>
        <div className="flex gap-4">
          <Dot on={ears.native ? ears.connected : null} label={ears.native ? (ears.connected === false ? "AirPods out" : "AirPods") : "no head tracking"} />
        </div>
      </div>

      {step >= 0 && <Steps step={step} paid={lastPayout?.status === "paid"} />}

      {/* orb */}
      <div className="relative mx-auto mt-6 grid h-[300px] w-[300px] place-items-center">
        {phase === "briefing" && (
          <>
            <span className="pulse-ring absolute inset-8 rounded-full border border-glow/40" />
            <span className="pulse-ring absolute inset-8 rounded-full border border-glow/30 [animation-delay:0.6s]" />
          </>
        )}
        <svg viewBox="0 0 300 300" className="absolute inset-0 -rotate-90">
          <circle cx="150" cy="150" r={R} fill="none" stroke="rgba(255,255,255,.07)" strokeWidth="6" />
          {phase === "armed" && (
            <circle
              cx="150"
              cy="150"
              r={R}
              fill="none"
              stroke="var(--glow)"
              strokeWidth="6"
              strokeLinecap="round"
              strokeDasharray={C}
              strokeDashoffset={C * (1 - left / windowS)}
              style={{ transition: "stroke-dashoffset 100ms linear" }}
            />
          )}
        </svg>
        <div className={result === "refused" ? "shake" : ""}>
          <Head pose={ears.pose} lit={lit} />
        </div>
        {phase === "armed" && <span className="num absolute bottom-6 text-[13px] text-glow/80">{left.toFixed(1)}s</span>}
        {phase === "done" && (
          <span className={`stamp stamp-in absolute text-[30px] ${result === "approved" ? "text-emerald-400" : "text-[#ff6b52]"}`}>{result}</span>
        )}
      </div>

      <section className="mx-auto max-w-md px-6 text-center">
        <h1 className="font-display text-[42px] leading-none text-white">{status.big}</h1>
        <p className="mx-auto mt-2 max-w-xs text-[14px] leading-snug text-white/55">{status.small}</p>

        {approval && (
          <div className="rise mt-6 rounded-3xl border border-white/10 bg-white/[0.04] p-5 text-left">
            <div className="flex items-baseline justify-between">
              <span className="num text-[34px] text-white">{inr(approval.amount)}</span>
              {approval.option_key && <span className="rounded-full bg-glow/15 px-2.5 py-1 text-[11px] font-bold text-glow">Option {approval.option_key}</span>}
            </div>
            <p className="text-[13px] text-white/50">
              to {party?.name ?? "payee"} · {payout?.to_account.masked}
            </p>
            <p className="mt-4 text-[17px] leading-relaxed text-white">{approval.brief}</p>
            <p className="mt-2 text-[13px] leading-relaxed text-white/45">{approval.brief_en}</p>
            {approval.pin_required && (
              <input
                inputMode="numeric"
                maxLength={6}
                value={pin}
                onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
                placeholder="PIN (above limit)"
                className="num mt-4 w-full rounded-xl border border-white/15 bg-transparent px-4 py-3 text-center text-[18px] tracking-[0.5em] text-white placeholder:tracking-normal placeholder:text-white/30"
              />
            )}
          </div>
        )}

        {phase === "done" && lastPayout && (
          <div className="rise mt-6 rounded-3xl border border-white/10 bg-white/[0.04] p-5 text-left">
            <div className="flex items-start justify-between gap-3">
              <div>
                <span className="num text-[30px] text-white">{inr(lastPayout.amount)}</span>
                <p className="text-[13px] text-white/50">to {lastParty?.name ?? "payee"} · {lastPayout.to_account.masked}</p>
              </div>
              <span className="rounded-xl bg-paper px-1.5 py-0.5">
                <Stamp status={lastPayout.status} className="text-[13px]" />
              </span>
            </div>
            {lastPayout.cf_transfer_id && <p className="num mt-3 text-[11px] text-white/40">Cashfree transfer {lastPayout.cf_transfer_id}</p>}
            <Link href={`/flow?id=${lastPayout.id}`} className="mt-4 block rounded-2xl border border-white/15 py-3 text-center text-[14px] font-semibold text-white">
              See how it was decided →
            </Link>
          </div>
        )}

        {phase === "idle" && <Recent snap={snap} />}

        {said && (
          <p className={`rise mt-4 rounded-2xl p-4 text-left text-[14px] leading-relaxed ${said.tone === "refuse" ? "bg-[#ff6b52]/10 text-[#ffb3a6]" : "bg-white/5 text-white/70"}`}>
            {said.text}
          </p>
        )}
        {notice && (
          <p className={`mt-3 text-[13px] ${notice.tone === "bad" ? "text-[#ff8a75]" : notice.tone === "ok" ? "text-emerald-300" : "text-white/60"}`}>{notice.text}</p>
        )}

        {/* controls: screen fallback, always available */}
        {approval && (
          <div className="mt-6 grid grid-cols-2 gap-3">
            {phase === "expired" ? (
              <button onClick={() => brief(approval)} className="col-span-2 rounded-2xl bg-glow py-3.5 font-bold text-night">
                Replay brief
              </button>
            ) : (
              <>
                <button
                  disabled={phase !== "armed" && phase !== "briefing"}
                  onClick={() => (ears.native ? handle("shake") : respond("refuse", "screen"))}
                  className="rounded-2xl border border-white/15 py-3.5 font-semibold text-white disabled:opacity-30"
                >
                  Refuse
                </button>
                <button
                  disabled={phase !== "armed" && phase !== "briefing"}
                  onClick={() => (ears.native ? handle("nod") : respond("approve", "screen"))}
                  className="rounded-2xl bg-emerald-500 py-3.5 font-bold text-night disabled:opacity-30"
                >
                  Approve
                </button>
              </>
            )}
          </div>
        )}
        {approval && phase === "armed" && (
          <div className="mt-3 flex justify-center gap-5 text-[13px] text-white/55">
            <button onClick={explain} className="underline underline-offset-4">Why?</button>
            <button onClick={amend} className="underline underline-offset-4">Change amount</button>
            <button onClick={() => { stopAudio(); brief(approval); }} className="underline underline-offset-4">Replay</button>
          </div>
        )}

        <Legend last={ears.lastGesture?.g} />
        {!ears.native && (
          <div className="mt-4 flex justify-center gap-2 text-[11px] text-white/40">
            <span>simulate:</span>
            {(["nod", "shake", "tilt_right", "stem"] as const).map((g) => (
              <button key={g} onClick={() => ears.simulate(g)} className="rounded-full border border-white/15 px-2.5 py-1 text-white/60">
                {g.replace("_right", "")}
              </button>
            ))}
          </div>
        )}
      </section>
      <TabBar dark />
    </main>
  );
}

function Steps({ step, paid }: { step: number; paid: boolean }) {
  const labels = ["Listen", "Nod", paid ? "Paid" : "Pay"];
  return (
    <ol className="mx-auto mt-4 flex max-w-xs items-center justify-center gap-2 text-[11px] font-semibold tracking-wide">
      {labels.map((l, i) => (
        <li key={l} className="flex items-center gap-2">
          <span
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 ${
              i < step || (i === 2 && paid) ? "bg-emerald-400/15 text-emerald-300" : i === step ? "bg-glow/20 text-glow" : "text-white/30"
            }`}
          >
            {i < step || (i === 2 && paid) ? "✓" : i + 1} {l}
          </span>
          {i < 2 && <span className="h-px w-4 bg-white/15" />}
        </li>
      ))}
    </ol>
  );
}

function Recent({ snap }: { snap: ReturnType<typeof useTradeFlow>["snap"] }) {
  const recent = [...(snap?.payouts ?? [])].reverse().slice(0, 3);
  if (!recent.length) return null;
  return (
    <div className="mt-8 text-left">
      <p className="mb-2 px-1 text-[11px] font-semibold tracking-[0.2em] text-white/40 uppercase">Recent</p>
      <ul className="space-y-2">
        {recent.map((p) => (
          <li key={p.id}>
            <Link href={`/flow?id=${p.id}`} className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
              <span className="min-w-0">
                <span className="num block text-[17px] text-white">{inr(p.amount)}</span>
                <span className="block truncate text-[12px] text-white/45">{snap?.parties[p.party_id]?.name ?? "payee"}</span>
              </span>
              <span className={`shrink-0 text-[12px] font-semibold ${p.status === "paid" ? "text-emerald-300" : p.status === "refused" || p.status === "failed" ? "text-[#ff8a75]" : "text-glow"}`}>
                {p.status.replace("_", " ")} ›
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Legend({ last }: { last?: EarsEvent }) {
  const items: { g: EarsEvent; icon: string; label: string }[] = [
    { g: "nod", icon: "↕", label: "Approve" },
    { g: "shake", icon: "↔", label: "Refuse" },
    { g: "tilt_right", icon: "↻", label: "Why?" },
    { g: "stem", icon: "◉", label: "Change" },
  ];
  return (
    <div className="mx-auto mt-8 grid max-w-xs grid-cols-4 gap-2">
      {items.map((it) => (
        <div key={it.g} className={`rounded-2xl py-2.5 transition ${last === it.g ? "bg-glow/20 text-glow" : "text-white/40"}`}>
          <div className="font-display text-[20px] leading-6">{it.icon}</div>
          <div className="text-[11px]">{it.label}</div>
        </div>
      ))}
    </div>
  );
}

/** A calm head-and-AirPods glyph that mirrors live head motion. */
function Head({ pose, lit }: { pose: { pitch: number; yaw: number; roll: number }; lit: boolean }) {
  const clamp = (v: number, m: number) => Math.max(-m, Math.min(m, v));
  const rx = clamp(-pose.pitch, 0.6) * 57;
  const ry = clamp(pose.yaw, 0.8) * 57;
  const rz = clamp(pose.roll, 0.6) * 57;
  return (
    <div style={{ perspective: 600 }} className="relative h-[150px] w-[130px]">
      <div
        className="absolute inset-0 transition-transform duration-100 ease-out"
        style={{ transform: `rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(${rz}deg)`, transformStyle: "preserve-3d" }}
      >
        <svg viewBox="0 0 130 150" className="h-full w-full">
          <defs>
            <radialGradient id="face" cx="50%" cy="38%" r="65%">
              <stop offset="0%" stopColor="#2b3270" />
              <stop offset="100%" stopColor="#151a3c" />
            </radialGradient>
          </defs>
          <ellipse cx="65" cy="72" rx="46" ry="58" fill="url(#face)" stroke="rgba(255,255,255,.18)" />
          {/* eyes look the way you face */}
          <ellipse cx={50 + clamp(pose.yaw, 0.8) * 10} cy={66 - clamp(pose.pitch, 0.6) * 8} rx="4" ry="5" fill="rgba(255,255,255,.75)" />
          <ellipse cx={80 + clamp(pose.yaw, 0.8) * 10} cy={66 - clamp(pose.pitch, 0.6) * 8} rx="4" ry="5" fill="rgba(255,255,255,.75)" />
          <path d={`M52 ${98} Q65 ${104} 78 ${98}`} stroke="rgba(255,255,255,.35)" strokeWidth="2.5" fill="none" strokeLinecap="round" />
          {/* AirPods */}
          {[16, 114].map((x) => (
            <g key={x}>
              <circle cx={x} cy="74" r="8" fill={lit ? "#f4b740" : "#e9e6f7"} style={{ filter: lit ? "drop-shadow(0 0 8px #f4b740)" : undefined }} />
              <rect x={x - 2.5} y="78" width="5" height="18" rx="2.5" fill={lit ? "#f4b740" : "#e9e6f7"} />
            </g>
          ))}
        </svg>
      </div>
    </div>
  );
}
