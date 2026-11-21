"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import type { Kind } from "@/lib/api";

export function inr(n: number): string {
  const sign = n < 0 ? "-" : "";
  return `${sign}₹${Math.abs(Math.round(n)).toLocaleString("en-IN")}`;
}

export function Rupee({ n, className = "" }: { n: number; className?: string }) {
  return <span className={`num ${className}`}>{inr(n)}</span>;
}

const KIND_STYLE: Record<Kind, string> = {
  confirmed: "bg-stamp-green/10 text-stamp-green border-stamp-green/30",
  pending: "bg-turmeric/15 text-[#8a5a0b] border-turmeric/40",
  estimated: "bg-sky/10 text-sky border-sky/30 border-dashed",
  simulated: "bg-ink/5 text-ink-soft border-ink/20 border-dashed",
};

export function KindChip({ kind }: { kind: Kind }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${KIND_STYLE[kind]}`}>
      {kind}
    </span>
  );
}

const STATUS: Record<string, { label: string; tone: "green" | "red" | "amber" | "ink" }> = {
  paid: { label: "Paid", tone: "green" },
  refused: { label: "Refused", tone: "red" },
  failed: { label: "Failed", tone: "red" },
  reversed: { label: "Reversed", tone: "red" },
  submitted: { label: "Sent to bank", tone: "amber" },
  sent: { label: "At bank", tone: "amber" },
  unknown: { label: "Unknown", tone: "amber" },
  awaiting_owner: { label: "Awaiting nod", tone: "amber" },
  needs_call: { label: "Calling", tone: "amber" },
  checking: { label: "Checking", tone: "ink" },
};

export function Stamp({ status, className = "" }: { status: string; className?: string }) {
  const s = STATUS[status] ?? { label: status, tone: "ink" as const };
  const color = { green: "text-stamp-green", red: "text-stamp-red", amber: "text-[#a8670c]", ink: "text-ink-soft" }[s.tone];
  const final = ["paid", "refused", "failed", "reversed"].includes(status);
  return <span className={`stamp ${final ? "stamp-in" : ""} text-[13px] ${color} ${className}`}>{s.label}</span>;
}

export function Section({ title, aside, children, className = "" }: { title: string; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`rise ${className}`}>
      <div className="mb-2 flex items-baseline justify-between gap-3 px-1">
        <h2 className="font-display text-[22px] leading-none">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

const TABS = [
  { href: "/", label: "Room", glyph: "₹" },
  { href: "/ears", label: "Ears", glyph: "◖◗" },
  { href: "/translate", label: "Translate", glyph: "அ" },
  { href: "/phone", label: "Phone", glyph: "☏" },
  { href: "/scorecard", label: "Traps", glyph: "✓" },
];

export function TabBar({ dark = false }: { dark?: boolean }) {
  const path = usePathname();
  return (
    <nav
      className={`safe-bottom fixed inset-x-0 bottom-0 z-40 border-t backdrop-blur-md ${
        dark ? "border-white/10 bg-night/80 text-white/60" : "border-rule bg-paper/85 text-ink-soft"
      }`}
    >
      <div className="mx-auto flex max-w-xl justify-around pt-2">
        {TABS.map((t) => {
          const on = t.href === "/" ? path === "/" : path?.startsWith(t.href);
          return (
            <Link key={t.href} href={t.href} className={`flex w-16 flex-col items-center gap-0.5 text-[11px] ${on ? (dark ? "text-glow" : "text-ink") : ""}`}>
              <span className={`font-display text-[18px] leading-5 ${on ? "" : "opacity-70"}`}>{t.glyph}</span>
              <span className={on ? "font-semibold" : ""}>{t.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

export function Btn({
  children,
  onClick,
  tone = "ink",
  disabled,
  className = "",
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "ink" | "green" | "red" | "ghost" | "glow";
  disabled?: boolean;
  className?: string;
  type?: "button" | "submit";
}) {
  const t = {
    ink: "bg-ink text-paper hover:bg-[#151b3d]",
    green: "bg-stamp-green text-white",
    red: "bg-stamp-red text-white",
    ghost: "border border-rule bg-card text-ink",
    glow: "bg-glow text-night",
  }[tone];
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-xl px-4 py-2.5 text-[15px] font-semibold transition active:scale-[0.98] disabled:opacity-40 ${t} ${className}`}
    >
      {children}
    </button>
  );
}

export function Dot({ on, label }: { on: boolean | null; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] uppercase tracking-wider">
      <span className={`h-2 w-2 rounded-full ${on === null ? "bg-white/30" : on ? "bg-emerald-400 shadow-[0_0_8px_#34d399]" : "bg-stamp-red"}`} />
      {label}
    </span>
  );
}

export function roleName(role: string): string {
  return { truck_owner: "Truck owner", driver: "Driver", forwarder: "Freight forwarder", supplier: "Yarn supplier", buyer: "Buyer" }[role] ?? role;
}
