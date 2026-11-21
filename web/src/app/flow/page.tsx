"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { api, useQuery, useTradeFlow, type AuditEntry, type Payout, type Snapshot } from "@/lib/api";
import { Stamp, TabBar, inr, roleName } from "@/components/ui";

/**
 * Flow: one payment, step by step, with who acted at each step and the evidence they used.
 * Built from the payout's audit trail (GET /api/payouts/{id}/trail) and refreshed live.
 */

type StageKey = "bill" | "call" | "checks" | "route" | "owner" | "money";
type State = "done" | "now" | "bad" | "skipped" | "pending";

const STAGES: { key: StageKey; title: string; short: string; actor: string; idle: string }[] = [
  { key: "bill", title: "Read the request", short: "Read", actor: "Sarvam Vision · Prism", idle: "Started from a request, not a document." },
  { key: "call", title: "Phone the people involved", short: "Call", actor: "Voice agent · Sarvam 105B", idle: "No call was needed." },
  { key: "checks", title: "Safety checks", short: "Checks", actor: "Tier 0 · plain code", idle: "Waiting to run." },
  { key: "route", title: "Decide who decides", short: "Route", actor: "Prism → GLM 5.3", idle: "Not reached." },
  { key: "owner", title: "Ask Selvam", short: "Owner", actor: "Bulbul voice · AirPods", idle: "The owner wasn't needed." },
  { key: "money", title: "Pay and confirm", short: "Cashfree", actor: "Cashfree Payouts", idle: "No money moved." },
];

const CURRENT: Record<string, StageKey | undefined> = {
  checking: "checks",
  needs_call: "call",
  awaiting_owner: "owner",
  submitted: "money",
  sent: "money",
  unknown: "money",
};

function stageOf(e: AuditEntry, payoutId: string): StageKey | null {
  switch (e.kind) {
    case "invoice":
    case "voice_note":
    case "extract":
      return "bill";
    case "prism":
      return e.ref === payoutId ? "route" : "bill";
    case "call":
    case "transcript":
    case "findings":
    case "fact":
    case "uncertain":
      return "call";
    case "plan":
    case "request":
    case "rule":
    case "verify":
      return "checks";
    case "glm":
    case "disagree":
    case "decision":
      return "route";
    case "brief":
    case "arm":
    case "explain":
    case "amend":
    case "expire":
    case "owner":
    case "approve":
      return "owner";
    case "payout":
    case "status":
    case "webhook":
    case "refuse":
      return "money";
    default:
      return null;
  }
}

type Trail = { payout: Payout; entries: AuditEntry[] };

export default function Flow() {
  const { snap } = useTradeFlow();
  const q = useQuery("id");
  const [picked, setPicked] = useState<string | null>(null);
  const id = picked ?? q ?? snap?.payouts.at(-1)?.id ?? null;
  const [trail, setTrail] = useState<Trail | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // Refetch whenever anything new lands in the audit log, so the flow fills in live.
  const tick = `${id}:${snap?.audit.length ?? 0}:${snap?.payouts.find((p) => p.id === id)?.status ?? ""}`;
  useEffect(() => {
    if (!id) return;
    let live = true;
    api<Trail>(`/api/payouts/${id}/trail`)
      .then((t) => live && (setTrail(t), setErr(null)))
      .catch((e) => live && setErr((e as Error).message));
    return () => {
      live = false;
    };
  }, [id, tick]);

  function pick(pid: string) {
    setPicked(pid);
    window.history.replaceState(null, "", `/flow?id=${pid}`);
  }

  const shown = trail && trail.payout.id === id ? trail : null;

  return (
    <main className="mx-auto max-w-2xl px-5 pb-32">
      <header className="safe-top rise pt-5 pb-4">
        <Link href="/" className="text-[12px] uppercase tracking-[0.2em] text-ink-soft">
          ← Room
        </Link>
        <h1 className="mt-2 font-display text-[42px] leading-none">
          Flow<span className="text-turmeric">.</span>
        </h1>
        <p className="mt-1 text-[14px] text-ink-soft">How one payment moved: who acted at each step, and what they saw.</p>
      </header>

      {snap && snap.payouts.length > 1 && <Picker snap={snap} id={id} onPick={pick} />}
      {!snap && <p className="text-ink-soft">Loading…</p>}
      {snap && !snap.payouts.length && (
        <div className="slip p-6 text-center">
          <p className="font-display text-[22px]">No payments yet</p>
          <p className="mt-1 text-[14px] text-ink-soft">Start the demo in the Room. Each payment&apos;s flow appears here as it happens.</p>
          <Link href="/" className="mt-4 inline-block rounded-xl bg-ink px-4 py-2.5 text-[15px] font-semibold text-paper">
            Go to the Room
          </Link>
        </div>
      )}
      {err && <p className="text-stamp-red">{err}</p>}
      {shown && snap && <Pipeline trail={shown} snap={snap} />}
      <TabBar />
    </main>
  );
}

function Picker({ snap, id, onPick }: { snap: Snapshot; id: string | null; onPick: (id: string) => void }) {
  return (
    <div className="-mx-5 mb-4 flex gap-2 overflow-x-auto px-5 pb-1">
      {[...snap.payouts].reverse().map((p) => {
        const on = p.id === id;
        return (
          <button
            key={p.id}
            onClick={() => onPick(p.id)}
            className={`shrink-0 rounded-2xl border px-3.5 py-2 text-left transition ${on ? "border-ink bg-ink text-paper" : "border-rule bg-card text-ink"}`}
          >
            <span className="num block text-[15px]">{inr(p.amount)}</span>
            <span className={`block text-[11px] ${on ? "text-paper/70" : "text-ink-soft"}`}>
              {snap.parties[p.party_id]?.name ?? "payee"} · {p.status.replace("_", " ")}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function Pipeline({ trail, snap }: { trail: Trail; snap: Snapshot }) {
  const { payout, entries } = trail;
  const party = snap.parties[payout.party_id];
  const by: Record<StageKey, AuditEntry[]> = { bill: [], call: [], checks: [], route: [], owner: [], money: [] };
  for (const e of entries) {
    const s = stageOf(e, payout.id);
    if (s) by[s].push(e);
  }
  const t0 = entries.length ? Date.parse(entries[0].at) : 0;
  const current = CURRENT[payout.status];
  const final = ["paid", "refused", "failed", "reversed"].includes(payout.status);

  const states = {} as Record<StageKey, State>;
  STAGES.forEach((s, i) => {
    const list = by[s.key];
    const laterActive = STAGES.slice(i + 1).some((x) => by[x.key].length > 0 || x.key === current);
    if (s.key === current) states[s.key] = "now";
    else if (list.length) states[s.key] = stageFailed(s.key, list, payout) ? "bad" : "done";
    else states[s.key] = laterActive || final ? "skipped" : "pending";
  });

  return (
    <>
      <div className="slip rise mb-5 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="num text-[32px] leading-none">{inr(payout.amount)}</p>
            <p className="mt-1.5 truncate text-[13px] text-ink-soft">
              to {party?.name ?? payout.to_account.holder} · {party ? roleName(party.role) : ""} · {payout.to_account.masked}
            </p>
          </div>
          <Stamp status={payout.status} className="shrink-0 text-[17px]" />
        </div>
        <Metro states={states} />
        <p className="mt-3 text-[13px] leading-snug text-ink-soft">{headline(payout, states)}</p>
      </div>

      <ol className="relative">
        {STAGES.map((s, i) => (
          <StageCard key={s.key} n={i + 1} stage={s} state={states[s.key]} entries={by[s.key]} t0={t0} payout={payout} last={i === STAGES.length - 1} />
        ))}
      </ol>
    </>
  );
}

function stageFailed(key: StageKey, list: AuditEntry[], payout: Payout): boolean {
  if (key === "money") return list.some((e) => e.kind === "refuse") || ["failed", "reversed"].includes(payout.status);
  if (key === "checks") {
    const last = latestRules(list);
    return last.some((r) => !r.ok && r.blocking === "refuse");
  }
  return list.some((e) => e.kind === "disagree");
}

function headline(p: Payout, st: Record<StageKey, State>): string {
  switch (p.status) {
    case "paid":
      return "Paid. Cashfree confirmed the transfer; nothing here was marked paid by a model.";
    case "refused":
      return "Stopped before any money moved.";
    case "awaiting_owner":
      return "Waiting for Selvam's nod in the Ears screen.";
    case "needs_call":
      return "Waiting on a phone call before this can go further.";
    case "submitted":
    case "sent":
    case "unknown":
      return "Sent to Cashfree. Waiting for Cashfree to confirm.";
    case "failed":
      return "Cashfree reported the transfer failed.";
    default:
      return st.checks === "now" ? "Running the safety checks." : "In progress.";
  }
}

function Metro({ states }: { states: Record<StageKey, State> }) {
  return (
    <div className="mt-5">
      <div className="relative flex justify-between">
        <span className="absolute top-[11px] right-3 left-3 h-[2px] bg-rule" />
        {STAGES.map((s) => (
          <a key={s.key} href={`#stage-${s.key}`} className="relative z-10 flex w-12 flex-col items-center gap-1">
            <Node state={states[s.key]} size="sm" />
            <span className={`text-[10px] font-semibold tracking-wide ${states[s.key] === "skipped" || states[s.key] === "pending" ? "text-ink-soft/50" : "text-ink"}`}>{s.short}</span>
          </a>
        ))}
      </div>
    </div>
  );
}

function Node({ state, size = "md", n }: { state: State; size?: "sm" | "md"; n?: number }) {
  const d = size === "sm" ? "h-6 w-6 text-[11px]" : "h-8 w-8 text-[13px]";
  const look = {
    done: "bg-stamp-green text-white",
    now: "bg-turmeric text-night ring-4 ring-turmeric/25 animate-pulse",
    bad: "bg-stamp-red text-white",
    skipped: "border border-dashed border-ink/25 bg-paper text-ink-soft/50",
    pending: "border border-ink/20 bg-card text-ink-soft",
  }[state];
  const glyph = { done: "✓", now: "•", bad: "✕", skipped: "–", pending: n ?? "" }[state];
  return <span className={`grid shrink-0 place-items-center rounded-full font-bold ${d} ${look}`}>{glyph}</span>;
}

function StageCard({
  n,
  stage,
  state,
  entries,
  t0,
  payout,
  last,
}: {
  n: number;
  stage: (typeof STAGES)[number];
  state: State;
  entries: AuditEntry[];
  t0: number;
  payout: Payout;
  last: boolean;
}) {
  const quiet = state === "skipped" || state === "pending";
  const at = entries.length ? (Date.parse(entries[0].at) - t0) / 1000 : null;
  return (
    <li id={`stage-${stage.key}`} className="rise relative scroll-mt-6 pb-4 pl-12" style={{ animationDelay: `${n * 40}ms` }}>
      {!last && <span className={`absolute top-9 bottom-0 left-[15px] w-[2px] ${quiet ? "border-l-2 border-dashed border-rule bg-transparent" : "bg-rule"}`} />}
      <span className="absolute top-0.5 left-0">
        <Node state={state} n={n} />
      </span>
      <div className={quiet ? "opacity-60" : ""}>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <h3 className="font-display text-[21px] leading-tight">{stage.title}</h3>
          {at !== null && <span className="num text-[10px] text-ink-soft">+{at.toFixed(1)}s</span>}
        </div>
        <span className="mt-1 inline-block rounded-full bg-ink/[0.06] px-2 py-0.5 text-[10px] font-bold tracking-wider text-ink-soft uppercase">{stage.actor}</span>
        {quiet ? (
          <p className="mt-1.5 text-[13px] text-ink-soft">{state === "pending" ? "Not reached yet." : stage.idle}</p>
        ) : (
          <div className="slip mt-2.5 space-y-3 p-4">
            {state === "now" && !entries.length && <p className="text-[14px] text-[#8a5a0b]">Happening now…</p>}
            <Evidence stage={stage.key} entries={entries} payout={payout} />
          </div>
        )}
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// evidence per stage
// ---------------------------------------------------------------------------

function Evidence({ stage, entries, payout }: { stage: StageKey; entries: AuditEntry[]; payout: Payout }) {
  switch (stage) {
    case "bill":
      return <BillEvidence entries={entries} />;
    case "call":
      return <CallEvidence entries={entries} />;
    case "checks":
      return <ChecksEvidence entries={entries} />;
    case "route":
      return <RouteEvidence entries={entries} />;
    case "owner":
      return <OwnerEvidence entries={entries} />;
    case "money":
      return <MoneyEvidence entries={entries} payout={payout} />;
  }
}

function Line({ children, tone }: { children: ReactNode; tone?: "bad" | "good" }) {
  return <p className={`text-[14px] leading-snug ${tone === "bad" ? "text-stamp-red" : tone === "good" ? "text-stamp-green" : ""}`}>{children}</p>;
}

function Label({ children }: { children: ReactNode }) {
  return <p className="mb-1.5 text-[10px] font-bold tracking-[0.15em] text-ink-soft uppercase">{children}</p>;
}

function BillEvidence({ entries }: { entries: AuditEntry[] }) {
  return (
    <>
      {entries.map((e, i) => {
        const d = e.detail as Record<string, unknown>;
        if (e.kind === "invoice" && Array.isArray(d.line_items)) {
          return (
            <div key={i}>
              <Line>{e.summary}</Line>
              <table className="mt-2 w-full text-[13px]">
                <tbody>
                  {(d.line_items as [string, number][]).map(([label, amt]) => (
                    <tr key={label} className="border-t border-dashed border-rule">
                      <td className="py-1.5 text-ink-soft">{label}</td>
                      <td className="num py-1.5 text-right">{inr(amt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {typeof d.confidence === "number" && <p className="mt-1 text-[11px] text-ink-soft">Fields read with confidence {d.confidence.toFixed(2)}</p>}
            </div>
          );
        }
        if (e.kind === "prism" && d.probabilities) {
          return (
            <div key={i}>
              <Label>Prism: what is the extra charge?</Label>
              <Bars probs={d.probabilities as Record<string, number>} chosen={String(d.choice ?? "")} />
            </div>
          );
        }
        return <Line key={i}>{e.summary}</Line>;
      })}
    </>
  );
}

function CallEvidence({ entries }: { entries: AuditEntry[] }) {
  const [all, setAll] = useState(false);
  return (
    <>
      {entries.map((e, i) => {
        const d = e.detail as Record<string, unknown>;
        if (e.kind === "transcript" && typeof d.en === "string") {
          const turns = d.en
            .split("\n")
            .map((l) => l.match(/^([^:]{2,24}):\s*(.*)$/))
            .filter((m): m is RegExpMatchArray => !!m);
          const shown = all ? turns : turns.slice(0, 4);
          return (
            <div key={i}>
              <Label>The call ({turns.length} turns, in English)</Label>
              <div className="space-y-1.5">
                {shown.map((m, j) => {
                  const agent = m[1].toLowerCase().startsWith("agent");
                  return (
                    <div key={j} className={`flex ${agent ? "" : "justify-end"}`}>
                      <p className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-snug ${agent ? "rounded-bl-md bg-ink/[0.06]" : "rounded-br-md bg-sky/10 text-ink"}`}>
                        <span className="mb-0.5 block text-[9px] font-bold tracking-wider text-ink-soft uppercase">{agent ? "TradeFlow agent" : "Them"}</span>
                        {m[2]}
                      </p>
                    </div>
                  );
                })}
              </div>
              {turns.length > 4 && (
                <button onClick={() => setAll(!all)} className="mt-2 text-[12px] font-semibold underline underline-offset-4">
                  {all ? "Show less" : `Read the whole call (${turns.length - 4} more)`}
                </button>
              )}
            </div>
          );
        }
        if (e.kind === "findings") {
          return (
            <div key={i}>
              <Label>What Prism took from the call</Label>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(d).map(([k, v]) => (
                  <Finding key={k} k={k} v={v} />
                ))}
              </div>
            </div>
          );
        }
        if (e.kind === "call") return <Line key={i}>{e.summary.replace(/ in ([a-z]{2})-IN/, (_, l) => ` in ${l === "ta" ? "Tamil" : l === "hi" ? "Hindi" : l === "en" ? "English" : l}`)}</Line>;
        return (
          <Line key={i} tone={e.kind === "fact" ? "good" : undefined}>
            {e.summary}
          </Line>
        );
      })}
    </>
  );
}

function Finding({ k, v }: { k: string; v: unknown }) {
  const label = k.replace(/^p_/, "chance of ").replace(/_/g, " ");
  let text: string;
  let tone = "border-rule bg-card text-ink";
  if (typeof v === "boolean") {
    text = `${v ? "✓" : "✕"} ${label}`;
    tone = v ? "border-stamp-green/30 bg-stamp-green/10 text-stamp-green" : "border-stamp-red/30 bg-stamp-red/10 text-stamp-red";
  } else if (typeof v === "number") {
    text = `${label}: ${v <= 1 ? `${Math.round(v * 100)}%` : v}`;
  } else if (v === null || v === undefined) {
    text = `${label}: unsure`;
    tone = "border-turmeric/40 bg-turmeric/10 text-[#8a5a0b]";
  } else {
    text = `${label}: ${String(v)}`;
  }
  return <span className={`rounded-full border px-2.5 py-1 text-[12px] font-semibold ${tone}`}>{text}</span>;
}

const RULES: Record<string, string> = {
  amount_within_outstanding: "Amount is within what's owed",
  no_payout_in_flight: "No other payment already on its way",
  not_duplicate: "Not paid twice",
  totals_add_up: "The bill adds up",
  extraction_confident: "Bill was read clearly",
  payee_verified: "Payee's bank account is real",
  account_change_confirmed: "Account change confirmed by the real payee",
  new_account_verified: "New account checked with the bank",
  cash_available: "There's cash to pay it",
};

type RuleRow = { rule: string; ok: boolean; detail: string; blocking: string; reruns: number };

function latestRules(entries: AuditEntry[]): RuleRow[] {
  const map = new Map<string, RuleRow>();
  for (const e of entries) {
    if (e.kind !== "rule") continue;
    const d = e.detail as { rule?: string; ok?: boolean; detail?: string; blocking?: string };
    if (!d.rule) continue;
    const prev = map.get(d.rule);
    map.set(d.rule, { rule: d.rule, ok: !!d.ok, detail: d.detail ?? "", blocking: d.blocking ?? "", reruns: prev ? prev.reruns + 1 : 0 });
  }
  return [...map.values()];
}

function ChecksEvidence({ entries }: { entries: AuditEntry[] }) {
  const [open, setOpen] = useState(false);
  const rules = latestRules(entries);
  const failed = rules.filter((r) => !r.ok);
  const other = entries.filter((e) => e.kind !== "rule");
  const BLOCK: Record<string, string> = { refuse: "stops the payment", call: "someone must be phoned", ask_owner: "Selvam must decide" };
  return (
    <>
      {other.length > 0 && (
        <div>
          <Label>What was asked</Label>
          {other.map((e, i) => (
            <p key={i} className="text-[13px] leading-snug text-ink-soft">
              {e.summary}
            </p>
          ))}
        </div>
      )}
      {rules.length > 0 && (
        <div>
          <Label>
            {rules.length - failed.length} of {rules.length} checks passed
          </Label>
          <ul className="space-y-1">
            {rules
              .filter((r) => open || !r.ok)
              .map((r) => (
                <li key={r.rule} className="flex gap-2 text-[13px] leading-snug">
                  <span className={`mt-px font-bold ${r.ok ? "text-stamp-green" : "text-stamp-red"}`}>{r.ok ? "✓" : "✕"}</span>
                  <span>
                    <span className="font-semibold">{RULES[r.rule] ?? r.rule}</span>
                    <span className="text-ink-soft"> · {r.detail}</span>
                    {!r.ok && BLOCK[r.blocking] && <span className="text-stamp-red"> → {BLOCK[r.blocking]}</span>}
                    {r.reruns > 0 && <span className="text-ink-soft"> (re-checked)</span>}
                  </span>
                </li>
              ))}
          </ul>
          <button onClick={() => setOpen(!open)} className="mt-2 text-[12px] font-semibold underline underline-offset-4">
            {open ? "Hide passed checks" : `Show all ${rules.length} checks`}
          </button>
        </div>
      )}
    </>
  );
}

const ROUTE: Record<string, string> = { auto: "Pay automatically", ask_owner: "Ask Selvam", deep: "Think harder (GLM)", refuse: "Refuse", call: "Phone someone" };

function RouteEvidence({ entries }: { entries: AuditEntry[] }) {
  return (
    <>
      {entries.map((e, i) => {
        const d = e.detail as Record<string, unknown>;
        if (e.kind === "prism" && d.probabilities) {
          return (
            <div key={i}>
              <Label>Prism (~0.1 s): who should decide?</Label>
              <Bars probs={d.probabilities as Record<string, number>} chosen={String(d.choice ?? "")} names={ROUTE} threshold={0.8} />
              {typeof d.confidence === "number" && d.confidence < 0.8 && (
                <p className="mt-1.5 text-[12px] text-[#8a5a0b]">Confidence {d.confidence.toFixed(2)} is under 0.8, so GLM was asked to think it through.</p>
              )}
            </div>
          );
        }
        if (e.kind === "glm") {
          const reasons = Array.isArray(d.reasons) ? (d.reasons as string[]) : [];
          return (
            <div key={i}>
              <Label>GLM 5.3: trade-offs</Label>
              {d.recommend ? <Line>Recommends option {String(d.recommend)}</Line> : null}
              <ol className="mt-1.5 space-y-1">
                {reasons.map((r, j) => (
                  <li key={j} className="flex gap-2 text-[13px] leading-snug">
                    <span className="num text-ink-soft">{j + 1}</span>
                    <span>{r}</span>
                  </li>
                ))}
              </ol>
            </div>
          );
        }
        if (e.kind === "decision") {
          const reasons = String(d.reason ?? "")
            .split(";")
            .map((s) => s.trim())
            .filter(Boolean);
          return (
            <div key={i}>
              <Label>Decision</Label>
              <span className="inline-block rounded-full bg-ink px-3 py-1 text-[12px] font-bold text-paper">{ROUTE[String(d.route)] ?? String(d.route)}</span>
              {reasons.length > 0 && (
                <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-[13px] text-ink-soft">
                  {reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              )}
            </div>
          );
        }
        if (e.kind === "disagree") return <Line key={i} tone="bad">{e.summary}</Line>;
        return <Line key={i}>{e.summary}</Line>;
      })}
    </>
  );
}

function OwnerEvidence({ entries }: { entries: AuditEntry[] }) {
  return (
    <ul className="space-y-2.5">
      {entries.map((e, i) => {
        const d = e.detail as Record<string, unknown>;
        if (e.kind === "brief") {
          return (
            <li key={i}>
              <Label>Brief, spoken in his AirPods</Label>
              {typeof d.spoken === "string" && <p className="text-[15px] leading-relaxed">{d.spoken}</p>}
              <p className="mt-1 text-[12px] leading-snug text-ink-soft">{e.summary.replace(/^Asked Selvam:\s*/, "")}</p>
            </li>
          );
        }
        if (e.kind === "owner") {
          const nod = /nod/.test(e.summary);
          const airpods = /airpods/i.test(e.summary);
          return (
            <li key={i} className="flex items-center gap-2.5 rounded-xl bg-stamp-green/10 px-3 py-2.5">
              <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-stamp-green font-display text-[18px] text-white">
                {nod ? "↕" : /shake|refus/.test(e.summary) ? "↔" : "✓"}
              </span>
              <span className="text-[14px] leading-snug">
                <span className="font-semibold">{e.summary.replace(/airpods/gi, "AirPods")}</span>
                {airpods && d.ears_in === true && <span className="block text-[12px] text-ink-soft">AirPods were in his ears; one-time approval token used.</span>}
              </span>
            </li>
          );
        }
        if (e.kind === "arm") return <li key={i} className="text-[13px] text-ink-soft">⏱ Brief finished, 10-second nod window opened</li>;
        if (e.kind === "expire") return <li key={i} className="text-[13px] text-[#8a5a0b]">⏱ {e.summary}</li>;
        return (
          <li key={i} className="text-[14px]">
            {e.summary}
          </li>
        );
      })}
    </ul>
  );
}

function MoneyEvidence({ entries, payout }: { entries: AuditEntry[]; payout: Payout }) {
  const refused = entries.find((e) => e.kind === "refuse");
  if (refused) {
    return (
      <>
        <Line tone="bad">⛔ {refused.summary}</Line>
        {payout.reason_spoken && <p className="mt-1.5 text-[13px] text-ink-soft">Spoken to Selvam: “{payout.reason_spoken}”</p>}
        <p className="mt-1.5 text-[13px] font-semibold">No money moved.</p>
      </>
    );
  }
  const steps = entries.filter((e) => e.kind === "payout" || e.kind === "status" || e.kind === "webhook");
  return (
    <>
      {(payout.transfer_id || payout.cf_transfer_id) && (
        <div className="mb-2 grid grid-cols-2 gap-2 text-[12px]">
          {payout.transfer_id && (
            <div className="rounded-lg bg-ink/[0.04] px-2.5 py-1.5">
              <span className="block text-[9px] font-bold tracking-wider text-ink-soft uppercase">Our transfer id</span>
              <span className="num break-all">{payout.transfer_id}</span>
            </div>
          )}
          {payout.cf_transfer_id && (
            <div className="rounded-lg bg-ink/[0.04] px-2.5 py-1.5">
              <span className="block text-[9px] font-bold tracking-wider text-ink-soft uppercase">Cashfree id</span>
              <span className="num break-all">{payout.cf_transfer_id}</span>
            </div>
          )}
        </div>
      )}
      <ul className="space-y-1.5">
        {steps.map((e, i) => {
          const d = e.detail as Record<string, unknown>;
          const ok = d.status === "SUCCESS";
          const bad = ["FAILED", "REJECTED", "REVERSED"].includes(String(d.status));
          const via = e.summary.startsWith("poll") ? "Cashfree status check" : e.kind === "payout" ? "Submitted" : "Cashfree webhook";
          return (
            <li key={i} className="flex gap-2 text-[13px] leading-snug">
              <span className={`font-bold ${ok ? "text-stamp-green" : bad ? "text-stamp-red" : "text-[#a8670c]"}`}>{ok ? "✓" : bad ? "✕" : "→"}</span>
              <span>
                <span className="font-semibold">{via}:</span> {String(d.status ?? "")}
                {d.status_code ? ` / ${String(d.status_code)}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
      {payout.simulated ? (
        <p className="mt-2 text-[11px] text-ink-soft">Simulated rails: same webhook shapes as Cashfree, no real API call.</p>
      ) : (
        <p className="mt-2 text-[11px] text-ink-soft">Cashfree sandbox. Status comes only from Cashfree, never from a model.</p>
      )}
    </>
  );
}

function Bars({ probs, chosen, names, threshold }: { probs: Record<string, number>; chosen: string; names?: Record<string, string>; threshold?: number }) {
  const rows = Object.entries(probs).sort((a, b) => b[1] - a[1]);
  return (
    <div className="space-y-1.5">
      {rows.map(([k, p]) => {
        const on = k === chosen;
        return (
          <div key={k} className="grid grid-cols-[minmax(0,9rem)_1fr_2.75rem] items-center gap-2 text-[12px]">
            <span className={`truncate ${on ? "font-bold" : "text-ink-soft"}`}>{names?.[k] ?? k.replace(/_/g, " ")}</span>
            <span className="relative h-2 overflow-hidden rounded-full bg-ink/[0.07]">
              <span className={`absolute inset-y-0 left-0 rounded-full ${on ? "bg-ink" : "bg-ink/25"}`} style={{ width: `${Math.max(2, p * 100)}%` }} />
              {threshold !== undefined && <span className="absolute inset-y-[-2px] w-px bg-stamp-red/60" style={{ left: `${threshold * 100}%` }} />}
            </span>
            <span className={`num text-right ${on ? "font-bold" : "text-ink-soft"}`}>{Math.round(p * 100)}%</span>
          </div>
        );
      })}
      {threshold !== undefined && <p className="text-[10px] text-stamp-red/80">red line = 0.8 confidence needed to act without GLM</p>}
    </div>
  );
}
