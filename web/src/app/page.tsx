"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { api, LANGS, useTradeFlow, type LiveEvent, type Option, type Snapshot } from "@/lib/api";
import { Btn, KindChip, Rupee, Section, Stamp, TabBar, inr, roleName } from "@/components/ui";

export default function ControlRoom() {
  const [toast, setToast] = useState<LiveEvent | null>(null);
  const { snap, online } = useTradeFlow((e) => {
    if (["say", "ring", "approval", "link", "collected", "payout"].includes(e.event)) {
      setToast(e);
      setTimeout(() => setToast((t) => (t === e ? null : t)), 6000);
    }
  });

  if (!snap) {
    return (
      <main className="grid min-h-dvh place-items-center p-8 text-center">
        <div>
          <p className="font-display text-4xl">TradeFlow</p>
          <p className="mt-2 text-ink-soft">{online ? "Loading…" : "Waiting for the backend on :8000"}</p>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-xl px-4 pb-28 sm:max-w-3xl">
      <Header snap={snap} online={online} />
      <Cash snap={snap} />
      <Script snap={snap} />
      <Options snap={snap} />
      <Payouts snap={snap} />
      <Obligations snap={snap} />
      <Feed snap={snap} />
      {toast && <Toast e={toast} onClose={() => setToast(null)} />}
      <TabBar />
    </main>
  );
}

function Header({ snap, online }: { snap: Snapshot; online: boolean }) {
  return (
    <header className="safe-top rise pt-4 pb-5">
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-soft">
          {snap.merchant.name} · {snap.merchant.city}
        </span>
        <span className={`inline-flex items-center gap-1.5 text-[11px] ${online ? "text-stamp-green" : "text-stamp-red"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${online ? "bg-stamp-green" : "bg-stamp-red"}`} /> {online ? "live" : "offline"}
        </span>
      </div>
      <h1 className="mt-1 font-display text-[44px] leading-[0.95]">
        Money control room<span className="text-turmeric">.</span>
      </h1>
      <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
        <Chip on={!snap.mode.rails.startsWith("sim")}>Cashfree: {snap.mode.rails}</Chip>
        <Chip on={snap.mode.calls === "phone"}>Calls: {snap.mode.calls}</Chip>
        <Chip on={snap.mode.sarvam}>Sarvam {snap.mode.sarvam ? "connected" : "missing key"}</Chip>
        <Link href="/scorecard" className="rounded-full border border-ink/20 px-2.5 py-1 text-ink hover:bg-ink/5">
          Trap scorecard →
        </Link>
      </div>
    </header>
  );
}

function Chip({ on, children }: { on: boolean; children: React.ReactNode }) {
  return <span className={`rounded-full border px-2.5 py-1 ${on ? "border-stamp-green/40 text-stamp-green" : "border-ink/20 border-dashed text-ink-soft"}`}>{children}</span>;
}

function Cash({ snap }: { snap: Snapshot }) {
  const c = snap.cash;
  return (
    <section className="slip rise relative mb-7 overflow-hidden p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[12px] uppercase tracking-[0.18em] text-ink-soft">Spendable now</p>
          <p className="num mt-1 text-[40px] font-medium leading-none">{inr(c.spendable)}</p>
        </div>
        <div className="text-right">
          <p className="text-[12px] uppercase tracking-[0.18em] text-ink-soft">Due in 7 days</p>
          <p className={`num mt-1 text-[22px] ${c.due_7d > c.spendable ? "text-stamp-red" : ""}`}>{inr(c.due_7d)}</p>
        </div>
      </div>
      <div className="perforation my-4 -mx-5" />
      <ul className="space-y-2 text-[14px]">
        {c.lines.map((l, i) => (
          <li key={i} className="flex items-center justify-between gap-3">
            <span className="flex min-w-0 items-center gap-2">
              <KindChip kind={l.kind} />
              <span className="truncate">{l.label}</span>
            </span>
            <Rupee n={l.amount} className={l.kind === "estimated" ? "text-sky" : ""} />
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[11px] text-ink-soft">
        Estimated money is weighted by the buyer&apos;s record and never counted as spendable. Port storage: {snap.storage.free_hours_left}h free, then {inr(snap.storage.per_day)}/day.
      </p>
    </section>
  );
}

function Script({ snap }: { snap: Snapshot }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [lang, setLang] = useState("ta-IN");
  const [err, setErr] = useState<string | null>(null);
  const [picked, setOpen] = useState<{ i: number; at: number } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const hasInvoice = snap.obligations.some((o) => o.id === "fwd_inv");
  const fwdCall = snap.calls.find((c) => c.purpose === "forwarder_release");
  const truckPayout = snap.payouts.find((p) => p.obligation_id === "truck_bal");
  const buyerCall = snap.calls.find((c) => c.purpose === "buyer_collection");

  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(null);
    }
  };

  const steps = [
    {
      n: 1,
      title: "Forwarder invoice arrives",
      body: "₹50,000 against a ₹32,000 quote. The gap is flagged and the options appear.",
      done: hasInvoice,
      action: (
        <div className="flex flex-wrap gap-2">
          <Btn disabled={hasInvoice || !!busy} onClick={() => run("inv", () => api("/api/invoices", new FormData()))}>
            {busy === "inv" ? "Reading…" : "Use sample invoice"}
          </Btn>
          <Btn tone="ghost" disabled={hasInvoice || !!busy} onClick={() => file.current?.click()}>
            Upload PDF/photo
          </Btn>
          <input
            ref={file}
            type="file"
            accept="image/*,application/pdf"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              const fd = new FormData();
              fd.append("file", f);
              run("inv", () => api("/api/invoices", fd));
            }}
          />
        </div>
      ),
    },
    {
      n: 2,
      title: "Phone the forwarder",
      body: "A judge plays the forwarder on the Phone tab. The agent asks what the ₹18,000 is for and whether he'll release on ₹32,000.",
      done: fwdCall?.status === "done",
      action: (
        <div className="flex flex-wrap items-center gap-2">
          <select value={lang} onChange={(e) => setLang(e.target.value)} className="rounded-xl border border-rule bg-card px-3 py-2.5 text-[14px]">
            {Object.entries(LANGS).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <Btn disabled={!hasInvoice || !!fwdCall || !!busy} onClick={() => run("fwd", () => api("/api/calls/forwarder", { language: lang }))}>
            {fwdCall ? `Call ${fwdCall.status}` : "Ring forwarder"}
          </Btn>
          <Link href="/phone?as=forwarder" className="text-[13px] underline underline-offset-4">
            open judge phone →
          </Link>
        </div>
      ),
    },
    {
      n: 3,
      title: "Selvam nods",
      body: "He hears the brief in Tamil in his AirPods and nods. Cashfree pays ₹32,000; the webhook confirms.",
      done: snap.payouts.some((p) => p.obligation_id === "fwd_inv" && p.status === "paid"),
      action: (
        <Link href="/ears" className="inline-block rounded-xl bg-night px-4 py-2.5 text-[15px] font-semibold text-glow">
          Open Ears {snap.approvals.length ? `· ${snap.approvals.length} waiting` : ""}
        </Link>
      ),
    },
    {
      n: 4,
      title: "Driver asks for his brother's account",
      body: "TradeFlow phones the truck owner on his saved number. He denies it. No money moves.",
      done: truckPayout?.status === "refused",
      action: (
        <div className="flex flex-wrap items-center gap-2">
          <Btn
            disabled={!!truckPayout || !!busy}
            onClick={() => run("drv", () => api("/api/driver-note", { text: "அண்ணா, பேலன்ஸ் பணத்தை என் தம்பி செந்தில் அக்கவுண்டுக்கு போடுங்க" }))}
          >
            {busy === "drv" ? "Listening…" : "Play driver voice note"}
          </Btn>
          <Link href="/phone?as=truck_owner" className="text-[13px] underline underline-offset-4">
            truck owner&apos;s phone →
          </Link>
        </div>
      ),
    },
    {
      n: 5,
      title: "Buyer call with a payment link",
      body: "The agent confirms the date and sends a Cashfree link during the call. Pay ₹1 and watch cash update.",
      done: snap.collections.some((c) => c.received > 0),
      action: (
        <div className="flex flex-wrap items-center gap-2">
          <Btn disabled={!!buyerCall || !!busy} onClick={() => run("buy", () => api("/api/calls/buyer", { language: "en-IN" }))}>
            {buyerCall ? `Call ${buyerCall.status}` : "Ring buyer"}
          </Btn>
          <Link href="/phone?as=buyer" className="text-[13px] underline underline-offset-4">
            buyer&apos;s phone →
          </Link>
          {snap.collections[0]?.link_url && (
            <a href={snap.collections[0].link_url} target="_blank" className="text-[13px] font-semibold text-sky underline underline-offset-4">
              open payment link
            </a>
          )}
        </div>
      ),
    },
  ];

  const firstOpen = Math.max(0, steps.findIndex((s) => !s.done));
  // a step the user tapped stays open until the demo moves on, then focus follows the next step
  const open = picked && picked.at === firstOpen ? picked.i : null;
  return (
    <Section
      title="Demo script"
      className="mb-8"
      aside={
        <button className="text-[12px] text-ink-soft underline underline-offset-4" onClick={() => run("reset", () => api("/api/demo/reset", {}))}>
          reset
        </button>
      }
    >
      <ol className="slip overflow-hidden">
        {steps.map((s, i) => {
          const current = (open ?? firstOpen) === i;
          return (
            <li key={s.n} className={`border-b border-dashed border-rule last:border-0 ${current ? "bg-card" : ""}`}>
              <button onClick={() => setOpen({ i, at: firstOpen })} className="flex w-full items-center gap-3 px-4 py-3 text-left">
                <span
                  className={`grid h-7 w-7 shrink-0 place-items-center rounded-full font-display text-[14px] ${
                    s.done ? "bg-stamp-green text-white" : current ? "bg-ink text-paper" : "border border-ink/25 text-ink-soft"
                  }`}
                >
                  {s.done ? "✓" : s.n}
                </span>
                <span className={`flex-1 font-semibold ${!current && !s.done ? "text-ink-soft" : ""} ${s.done && !current ? "line-through decoration-ink/20" : ""}`}>{s.title}</span>
                {!current && <span className="text-ink-soft/50">›</span>}
              </button>
              {current && (
                <div className="rise px-4 pb-4 pl-14">
                  <p className="text-[13px] leading-snug text-ink-soft">{s.body}</p>
                  <div className="mt-3">{s.action}</div>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {err && <p className="mt-2 rounded-lg bg-stamp-red/10 p-3 text-[13px] text-stamp-red">{err}</p>}
    </Section>
  );
}

function Options({ snap }: { snap: Snapshot }) {
  if (!snap.options.some((o) => o.key !== "A") ) return null;
  return (
    <Section title="What can we pay?" className="mb-8" aside={<span className="text-[11px] text-ink-soft">arithmetic by code, ranked by rules</span>}>
      <div className="grid gap-3 sm:grid-cols-3">
        {snap.options.map((o, i) => (
          <OptionCard key={o.key} o={o} top={i === 0} />
        ))}
      </div>
    </Section>
  );
}

function OptionCard({ o, top }: { o: Option; top: boolean }) {
  return (
    <article
      className={`slip relative p-4 transition-all ${top ? "ring-2 ring-ink" : ""} ${!o.feasible ? "opacity-60" : ""}`}
      style={{ transform: top ? "translateY(-2px)" : undefined }}
    >
      <div className="flex items-baseline justify-between">
        <span className="font-display text-[34px] leading-none">{o.key}</span>
        {top && <span className="rounded-full bg-ink px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-paper">best now</span>}
        {!o.feasible && <span className="stamp text-[11px] text-stamp-red">Short</span>}
      </div>
      <p className="mt-1 text-[13px] font-semibold leading-snug">{o.title}</p>
      <dl className="mt-3 space-y-1 text-[13px]">
        {o.pays.map((p) => (
          <div key={p.obligation_id} className="flex justify-between gap-2">
            <dt className="truncate text-ink-soft">{p.label}</dt>
            <dd className="num">{inr(p.amount)}</dd>
          </div>
        ))}
        <div className="flex justify-between border-t border-dashed border-rule pt-1 font-semibold">
          <dt>Pays now</dt>
          <dd className="num">{inr(o.pays_now)}</dd>
        </div>
        <div className="flex justify-between font-semibold">
          <dt>Cash left</dt>
          <dd className={`num ${o.cash_left < 0 ? "text-stamp-red" : "text-stamp-green"}`}>{inr(o.cash_left)}</dd>
        </div>
      </dl>
      {o.catch && <p className={`mt-3 text-[12px] leading-snug ${o.needs ? "text-[#8a5a0b]" : "text-ink-soft"}`}>{o.needs ? "⧗ " : ""}{o.catch}</p>}
    </article>
  );
}

function Payouts({ snap }: { snap: Snapshot }) {
  if (!snap.payouts.length) return null;
  return (
    <Section title="Payouts" className="mb-8">
      <ul className="space-y-2.5">
        {snap.payouts.map((p) => {
          const party = snap.parties[p.party_id];
          return (
            <li key={p.id} className="slip rise flex items-center gap-3 p-4">
              <div className="min-w-0 flex-1">
                <p className="num text-[20px]">{inr(p.amount)}</p>
                <p className="truncate text-[13px] text-ink-soft">
                  {party?.name} · {p.to_account.masked} · {p.requested_by}
                </p>
                {p.reason && p.status === "refused" && <p className="mt-1 text-[12px] text-stamp-red">{p.reason}</p>}
                {p.reason_spoken && p.status === "refused" && <p className="mt-0.5 text-[12px] text-ink-soft">“{p.reason_spoken}”</p>}
                <Link href={`/flow?id=${p.id}`} className="mt-1.5 inline-flex items-center gap-1 rounded-full bg-ink/[0.06] px-2.5 py-1 text-[12px] font-semibold">
                  See how it was decided →
                </Link>
                {p.simulated && <span className="ml-2 text-[10px] uppercase tracking-wider text-ink-soft">simulated rails</span>}
              </div>
              <Stamp status={p.status} className="text-[15px]" />
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function Obligations({ snap }: { snap: Snapshot }) {
  return (
    <Section title="Obligations" className="mb-8">
      <div className="slip divide-y divide-dashed divide-rule">
        {snap.obligations.map((o) => {
          const party = snap.parties[o.party_id];
          return (
            <div key={o.id} className="flex items-center justify-between gap-3 p-4">
              <div className="min-w-0">
                <p className="truncate font-semibold">{o.label}</p>
                <p className="text-[12px] text-ink-soft">
                  {party?.name} · {roleName(party?.role ?? "")} · due {new Date(o.due).toLocaleString("en-IN", { weekday: "short", hour: "numeric" })}
                  {o.msme_protected && " · Udyam, 45-day rule"}
                </p>
                {o.disputed > 0 && (
                  <p className="mt-0.5 text-[12px] text-stamp-red">
                    {inr(o.disputed)} above quote of {inr(o.quoted ?? 0)}
                  </p>
                )}
              </div>
              <div className="text-right">
                <Rupee n={o.outstanding} className="text-[16px]" />
                {o.paid > 0 && <p className="num text-[11px] text-stamp-green">paid {inr(o.paid)}</p>}
              </div>
            </div>
          );
        })}
        {snap.collections.map((c) => (
          <div key={c.id} className="flex items-center justify-between gap-3 p-4">
            <div>
              <p className="font-semibold">
                {c.label} <span className="text-[11px] text-sky">incoming</span>
              </p>
              <p className="text-[12px] text-ink-soft">
                {snap.parties[c.party_id]?.name} · expected {new Date(c.expected).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}
                {c.commitment && ` · on call: ${c.commitment.replace(/_/g, " ")}`}
              </p>
            </div>
            <div className="text-right">
              <Rupee n={c.amount - c.received} className="text-[16px] text-sky" />
              {c.received > 0 && <p className="num text-[11px] text-stamp-green">received {inr(c.received)}</p>}
            </div>
          </div>
        ))}
      </div>
    </Section>
  );
}

const KIND_GLYPH: Record<string, string> = {
  rule: "§",
  prism: "◇",
  glm: "◆",
  call: "☏",
  transcript: "❝",
  findings: "◇",
  fact: "✱",
  brief: "◖",
  owner: "◗",
  payout: "₹",
  status: "₹",
  refuse: "✕",
  verify: "✓",
  link: "↗",
  collected: "₹",
};

function Feed({ snap }: { snap: Snapshot }) {
  const [show, setShow] = useState(false);
  const items = [...snap.audit].reverse().slice(0, show ? 40 : 4);
  return (
    <Section
      title="Audit trail"
      aside={
        <button onClick={() => setShow(!show)} className="text-[12px] text-ink-soft underline underline-offset-4">
          {show ? "show less" : `show all ${snap.audit.length}`}
        </button>
      }
    >
      <ol className="slip max-h-[460px] overflow-y-auto p-2 text-[13px]">
        {items.map((e, i) => (
          <li key={`${e.at}-${i}`} className="flex gap-2.5 rounded-lg px-2 py-1.5 even:bg-paper/50">
            <span className={`w-4 shrink-0 text-center font-display ${e.kind === "refuse" || e.summary.startsWith("FAIL") ? "text-stamp-red" : "text-ink-soft"}`}>
              {KIND_GLYPH[e.kind] ?? "·"}
            </span>
            <span className="num w-[52px] shrink-0 text-[11px] leading-5 text-ink-soft">
              {new Date(e.at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false })}
            </span>
            <span className="min-w-0 leading-snug">{e.summary}</span>
          </li>
        ))}
      </ol>
    </Section>
  );
}

function Toast({ e, onClose }: { e: LiveEvent; onClose: () => void }) {
  const text: Record<string, string> = {
    ring: `Ringing ${e.data?.party} (${roleName(e.data?.role)})`,
    approval: "A payment is waiting for Selvam's nod",
    say: e.data?.text,
    link: `Payment link sent: ${inr(e.data?.amount ?? 0)}`,
    collected: `Buyer paid ${inr(e.data?.amount ?? 0)}`,
    payout: `Payout ${e.data?.id}: ${e.data?.status}`,
  };
  return (
    <button onClick={onClose} className="rise fixed inset-x-4 top-4 z-50 mx-auto max-w-md rounded-2xl bg-ink p-4 text-left text-[14px] text-paper shadow-2xl">
      {text[e.event] ?? e.event}
    </button>
  );
}
