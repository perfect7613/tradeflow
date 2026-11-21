"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

/** Same origin in production (FastAPI serves this export); localhost:8000 in `next dev`. */
export const API = process.env.NEXT_PUBLIC_API ?? "";

export function wsUrl(path: string): string {
  const base = API || (typeof window !== "undefined" ? window.location.origin : "");
  return base.replace(/^http/, "ws") + path;
}

export function audioUrl(id: string | null | undefined): string | null {
  return id ? `${API}/api/audio/${id}` : null;
}

export async function api<T = unknown>(path: string, body?: unknown, init: RequestInit = {}): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const r = await fetch(API + path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined || isForm ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
    ...init,
  });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const j = await r.json();
      msg = j.detail ?? JSON.stringify(j);
    } catch {}
    throw new Error(msg);
  }
  return r.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// types mirroring backend/src/tradeflow/service.py snapshot()
// ---------------------------------------------------------------------------

export type Kind = "confirmed" | "pending" | "estimated" | "simulated";

export interface Snapshot {
  merchant: { name: string; owner: string; city: string };
  mode: { rails: string; calls: string; sarvam: boolean; owner_language: string; approval_window_s: number };
  cash: {
    confirmed: number;
    pending_out: number;
    expected_in_weighted: number;
    due_7d: number;
    spendable: number;
    lines: { label: string; amount: number; kind: Kind }[];
  };
  storage: { free_hours_left: number; per_day: number };
  parties: Record<string, Party>;
  obligations: Obligation[];
  collections: Collection[];
  options: Option[];
  payouts: Payout[];
  approvals: Approval[];
  calls: Call[];
  facts: Record<string, { value: unknown; source: string; confidence: number | null }>;
  audit: AuditEntry[];
}

export interface Party {
  id: string;
  name: string;
  role: string;
  language: string;
  phone: string;
  account?: { masked: string; ifsc: string; holder: string };
  udyam?: string | null;
}
export interface Obligation {
  id: string;
  party_id: string;
  kind: string;
  label: string;
  amount: number;
  quoted: number | null;
  paid: number;
  outstanding: number;
  disputed: number;
  due: string;
  status: string;
  invoice_no: string | null;
  line_items: [string, number][];
  msme_protected: boolean;
  extraction_confidence: number;
  source: string;
}
export interface Collection {
  id: string;
  party_id: string;
  label: string;
  amount: number;
  received: number;
  expected: string;
  link_url: string | null;
  commitment: string | null;
}
export interface Option {
  key: string;
  title: string;
  pays: { obligation_id: string; label: string; amount: number }[];
  pays_now: number;
  cash_left: number;
  catch: string;
  feasible: boolean;
  needs: string | null;
  score: number;
}
export interface Payout {
  id: string;
  obligation_id: string;
  party_id: string;
  amount: number;
  to_account: { masked: string; ifsc: string; holder: string };
  requested_by: string;
  status: string;
  transfer_id: string | null;
  cf_transfer_id: string | null;
  reason: string | null;
  reason_spoken: string | null;
  simulated: boolean;
  created: string;
}
export interface Approval {
  id: string;
  payout_id: string;
  amount: number;
  option_key: string | null;
  brief_en: string;
  brief: string;
  explain_en: string[];
  token: string;
  pin_required: boolean;
  state: string;
  armed_at: string | null;
  audio_id: string | null;
}
export interface Turn {
  role: string;
  text: string;
  en: string | null;
}
export interface Call {
  id: string;
  purpose: string;
  party_id: string;
  language: string;
  channel: string;
  status: string;
  about: Record<string, unknown>;
  transcript: Turn[];
  findings: Record<string, unknown>;
  created: string;
}
export interface AuditEntry {
  at: string;
  kind: string;
  ref: string;
  summary: string;
  detail: Record<string, unknown>;
}

export interface LiveEvent {
  event: string;
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any
  at: string;
}

/**
 * Live snapshot: fetches /api/state and refetches whenever the server says something
 * changed. Other events (say, ring, approval, link) are passed to `onEvent`.
 */
export function useTradeFlow(onEvent?: (e: LiveEvent) => void) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [online, setOnline] = useState(false);
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSnap(await api<Snapshot>("/api/state"));
      setOnline(true);
    } catch {
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    const connect = () => {
      es = new EventSource(`${API}/api/events`);
      es.onopen = () => {
        setOnline(true);
        refresh(); // first load, and catch up after every reconnect
      };
      es.onmessage = (m) => {
        const e = JSON.parse(m.data) as LiveEvent;
        if (e.event === "changed" || e.event === "audit") {
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(refresh, 120);
        }
        if (e.event !== "audit" && e.event !== "changed") handler.current?.(e);
      };
      es.onerror = () => {
        setOnline(false);
        es?.close();
        retry = setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      es?.close();
      if (retry) clearTimeout(retry);
    };
  }, [refresh]);

  return { snap, online, refresh };
}

/** Read the URL query once on the client (static export pages have no server query). */
export function useQuery(key: string): string | null {
  return useSyncExternalStore(
    noop,
    () => new URLSearchParams(window.location.search).get(key),
    () => null,
  );
}
const noop = () => () => {};

export const LANGS: Record<string, string> = {
  "ta-IN": "தமிழ் Tamil",
  "hi-IN": "हिन्दी Hindi",
  "en-IN": "English",
  "kn-IN": "ಕನ್ನಡ Kannada",
  "te-IN": "తెలుగు Telugu",
  "ml-IN": "മലയാളം Malayalam",
  "mr-IN": "मराठी Marathi",
  "bn-IN": "বাংলা Bengali",
  "gu-IN": "ગુજરાતી Gujarati",
};
