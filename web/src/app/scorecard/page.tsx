"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { TabBar } from "@/components/ui";

interface Score {
  cases: { name: string; trap: boolean; verdict: string; caught: boolean; detail: string }[];
  traps_caught: number;
  traps_total: number;
  good_wrongly_blocked: number;
  good_total: number;
  note: string;
}

const VERDICT: Record<string, string> = { refuse: "refused", call: "called to check", ask_owner: "asked owner", pass: "paid" };

export default function Scorecard() {
  const [s, setS] = useState<Score | null>(null);
  useEffect(() => {
    api<Score>("/api/scorecard").then(setS).catch(() => {});
  }, []);

  return (
    <main className="mx-auto max-w-xl px-5 pb-28">
      <header className="safe-top pt-5 pb-6">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-ink-soft">Trap scorecard</p>
        <h1 className="mt-1 font-display text-[40px] leading-none">Planted problems.</h1>
        <p className="mt-2 text-[14px] text-ink-soft">Synthetic cases with one planted problem each, plus good payments that must still go through.</p>
      </header>
      {s && (
        <>
          <div className="grid grid-cols-2 gap-3">
            <div className="slip p-5">
              <p className="num text-[44px] leading-none text-stamp-green">
                {s.traps_caught}
                <span className="text-[22px] text-ink-soft">/{s.traps_total}</span>
              </p>
              <p className="mt-2 text-[13px] text-ink-soft">traps caught</p>
            </div>
            <div className="slip p-5">
              <p className={`num text-[44px] leading-none ${s.good_wrongly_blocked ? "text-stamp-red" : "text-stamp-green"}`}>
                {s.good_wrongly_blocked}
                <span className="text-[22px] text-ink-soft">/{s.good_total}</span>
              </p>
              <p className="mt-2 text-[13px] text-ink-soft">good payments wrongly blocked</p>
            </div>
          </div>
          <ul className="slip mt-5 divide-y divide-dashed divide-rule">
            {s.cases.map((c) => (
              <li key={c.name} className="flex gap-3 p-4">
                <span className={`mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full text-[12px] font-bold text-white ${c.caught ? "bg-stamp-green" : "bg-stamp-red"}`}>
                  {c.caught ? "✓" : "✕"}
                </span>
                <div className="min-w-0">
                  <p className="font-semibold leading-snug">{c.name}</p>
                  <p className="mt-0.5 text-[12px] text-ink-soft">
                    <span className={`font-semibold ${c.verdict === "pass" ? "text-stamp-green" : "text-stamp-red"}`}>{VERDICT[c.verdict] ?? c.verdict}</span> · {c.detail}
                  </p>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-[12px] text-ink-soft">{s.note}</p>
        </>
      )}
      <TabBar />
    </main>
  );
}
