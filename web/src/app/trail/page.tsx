"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, useQuery, type AuditEntry, type Payout } from "@/lib/api";
import { Stamp, TabBar, inr } from "@/components/ui";

const GROUP: Record<string, { label: string; tone: string }> = {
  request: { label: "Request", tone: "bg-ink" },
  invoice: { label: "Document", tone: "bg-ink" },
  voice_note: { label: "Voice note", tone: "bg-ink" },
  verify: { label: "Secure ID", tone: "bg-sky" },
  rule: { label: "Rule", tone: "bg-ink-soft" },
  prism: { label: "Prism", tone: "bg-turmeric" },
  glm: { label: "GLM", tone: "bg-turmeric" },
  disagree: { label: "Disagreement", tone: "bg-stamp-red" },
  decision: { label: "Route", tone: "bg-ink" },
  call: { label: "Call", tone: "bg-sky" },
  transcript: { label: "Transcript", tone: "bg-sky" },
  findings: { label: "Call findings", tone: "bg-turmeric" },
  fact: { label: "Fact", tone: "bg-turmeric" },
  brief: { label: "Brief", tone: "bg-glow" },
  arm: { label: "Nod window", tone: "bg-glow" },
  owner: { label: "Owner", tone: "bg-stamp-green" },
  explain: { label: "Explain", tone: "bg-glow" },
  payout: { label: "Cashfree", tone: "bg-stamp-green" },
  status: { label: "Webhook", tone: "bg-stamp-green" },
  refuse: { label: "Refused", tone: "bg-stamp-red" },
};

export default function Trail() {
  const [data, setData] = useState<{ payout: Payout; entries: AuditEntry[] } | null>(null);
  const [fetchErr, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const id = useQuery("id");
  const err = id === null ? "No payout selected." : fetchErr;

  useEffect(() => {
    if (!id) return;
    api<{ payout: Payout; entries: AuditEntry[] }>(`/api/payouts/${id}/trail`).then(setData).catch((e) => setErr(e.message));
  }, [id]);

  return (
    <main className="mx-auto max-w-xl px-5 pb-28">
      <header className="safe-top pt-5 pb-5">
        <Link href="/" className="text-[12px] uppercase tracking-[0.2em] text-ink-soft">
          ← Room
        </Link>
        <h1 className="mt-2 font-display text-[40px] leading-none">Why?</h1>
        <p className="mt-1 text-[14px] text-ink-soft">Every check, model answer and decision behind one payment.</p>
      </header>
      {err && <p className="text-stamp-red">{err}</p>}
      {data && (
        <>
          <div className="slip mb-6 flex items-center justify-between p-5">
            <div>
              <p className="num text-[30px] leading-none">{inr(data.payout.amount)}</p>
              <p className="mt-1 text-[13px] text-ink-soft">
                to {data.payout.to_account.holder} · {data.payout.to_account.masked}
              </p>
              {data.payout.transfer_id && <p className="num mt-1 text-[11px] text-ink-soft">transfer {data.payout.transfer_id}{data.payout.cf_transfer_id ? ` · cf ${data.payout.cf_transfer_id}` : ""}</p>}
            </div>
            <Stamp status={data.payout.status} className="text-[18px]" />
          </div>
          <ol className="relative ml-3 border-l-2 border-dashed border-rule">
            {data.entries.map((e, i) => {
              const g = GROUP[e.kind] ?? { label: e.kind, tone: "bg-ink-soft" };
              const fail = e.summary.startsWith("FAIL") || e.kind === "refuse";
              const hasDetail = e.detail && Object.keys(e.detail).length > 0;
              return (
                <li key={i} className="rise relative mb-4 pl-6" style={{ animationDelay: `${i * 25}ms` }}>
                  <span className={`absolute -left-[7px] top-1.5 h-3 w-3 rounded-full ring-4 ring-paper ${fail ? "bg-stamp-red" : g.tone}`} />
                  <div className="flex items-baseline gap-2">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-ink-soft">{g.label}</span>
                    <span className="num text-[10px] text-ink-soft/70">{new Date(e.at).toLocaleTimeString("en-IN", { hour12: false })}</span>
                  </div>
                  <p className={`mt-0.5 text-[14px] leading-snug ${fail ? "text-stamp-red" : ""}`}>{e.summary}</p>
                  {hasDetail && (
                    <button onClick={() => setOpen(open === i ? null : i)} className="mt-1 text-[12px] text-ink-soft underline underline-offset-4">
                      {open === i ? "hide detail" : "detail"}
                    </button>
                  )}
                  {open === i && <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-card p-3 text-[11px] leading-relaxed whitespace-pre-wrap">{JSON.stringify(e.detail, null, 2)}</pre>}
                </li>
              );
            })}
          </ol>
        </>
      )}
      <TabBar />
    </main>
  );
}
