"""TradeFlow: wires the desks together and owns the hero flow.

    invoice arrives -> gap flagged -> forwarder call -> facts -> re-plan -> owner brief -> nod -> payout
    driver voice note asks for a new account -> rules demand a call -> truck owner denies -> refusal
    buyer call -> payment link during the call -> webhook -> cash position updates
"""

from __future__ import annotations

import logging
import re
from typing import Any

from . import scenario, tier0
from .calls import CallDesk
from .config import Settings
from .domain import Account, Call, PayoutStatus, Turn
from .engine import Decision, Engine
from .ledger import Ledger
from .money import inr, mask
from .payments import PaymentDesk
from .rails import SimRails, make_rails
from .sarvam import Sarvam, SarvamError
from .speaker import Speaker

log = logging.getLogger(__name__)

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {"type": "string", "description": "Name of the company that issued the invoice"},
        "invoice_no": {"type": "string", "description": "Invoice number"},
        "total": {"type": "number", "description": "Grand total payable, in INR"},
        "tax": {"type": "number", "description": "Total GST/tax amount, in INR, 0 if none"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"description": {"type": "string"}, "amount": {"type": "number"}},
            },
        },
    },
}


class TradeFlow:
    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.sarvam = Sarvam(cfg)
        self.rails = make_rails(cfg)
        self.reset()

    def reset(self) -> None:
        self.L = Ledger()
        self.speaker = Speaker(self.sarvam)
        self.engine = Engine(self.L, self.sarvam, self.cfg)
        self.payments = PaymentDesk(self.L, self.engine, self.rails, self.speaker, self.cfg)
        self.calls = CallDesk(self.L, self.sarvam, self.engine, self.speaker, self.cfg)
        self.payments.on_needs_call = self._needs_call
        self.calls.on_finished = self._call_finished
        self.calls.make_link = self._make_link
        if isinstance(self.rails, SimRails):
            self.rails.sink = self.webhook
            self.rails.bank_names.update(scenario.BANK_NAMES)
            self.rails.transfers.clear()
        scenario.seed(self.L)

    # ------------------------------------------------------------------
    # snapshot for the UI
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        L = self.L
        cash = tier0.cash_position(L)
        opts = tier0.options(L)
        return {
            "merchant": L.merchant,
            "mode": {
                "rails": "simulated" if self.rails.simulated else f"cashfree-{self.cfg.cashfree_env}",
                "calls": "phone" if self.cfg.sarvam_phone_calls else "browser",
                "sarvam": self.sarvam.configured,
                "owner_language": self.cfg.owner_language,
                "approval_window_s": self.cfg.approval_window_s,
            },
            "cash": {**cash.model_dump(), "spendable": cash.spendable},
            "storage": L.storage,
            "parties": {k: _party_view(p) for k, p in L.parties.items()},
            "obligations": [o.model_dump(mode="json") | {"outstanding": o.outstanding, "disputed": o.disputed} for o in L.obligations.values()],
            "collections": [c.model_dump(mode="json") for c in L.collections.values()],
            "options": [o.model_dump() for o in opts],
            "payouts": [_payout_view(p) for p in sorted(L.payouts.values(), key=lambda p: p.created, reverse=True)],
            "approvals": [
                a.model_dump(mode="json") | {"audio_id": self.payments.brief_audio.get(a.id)}
                for a in L.approvals.values()
                if a.state in {"pending", "armed"}
            ],
            "calls": [c.model_dump(mode="json") for c in sorted(L.calls.values(), key=lambda c: c.created, reverse=True)],
            "facts": {k: f.model_dump() for k, f in L.facts.items()},
            "audit": [e.model_dump(mode="json") for e in L.audit_log[-60:]],
        }

    def trail(self, payout_id: str) -> dict[str, Any]:
        p = self.L.payouts[payout_id]
        refs = {payout_id}
        refs |= {a.id for a in self.L.approvals.values() if a.payout_id == payout_id}
        refs |= {c.id for c in self.L.calls.values() if c.about.get("payout_id") == payout_id or c.about.get("obligation_id") == p.obligation_id}
        refs.add(p.obligation_id)
        return {"payout": _payout_view(p), "entries": [e.model_dump(mode="json") for e in self.L.trail(*refs)]}

    # ------------------------------------------------------------------
    # 1. invoice
    # ------------------------------------------------------------------
    async def invoice(self, data: bytes | None = None, filename: str = "", quoted: int | None = None) -> dict[str, Any]:
        source = "sample"
        fields: dict[str, Any] = dict(scenario.SAMPLE_INVOICE)
        if data:
            try:
                got = await self.sarvam.extract_document(data, filename or "invoice.pdf", INVOICE_SCHEMA)
                fields = {
                    "vendor_name": got.get("vendor_name"),
                    "invoice_no": got.get("invoice_no"),
                    "total": int(float(got.get("total") or 0)),
                    "tax": int(float(got.get("tax") or 0)),
                    "line_items": [(i.get("description", ""), int(float(i.get("amount") or 0))) for i in got.get("line_items") or []],
                    "quoted": quoted or scenario.SAMPLE_INVOICE["quoted"],
                    "confidence": 0.85,
                }
                source = "sarvam-vision"
            except Exception as e:  # noqa: BLE001
                self.L.audit("extract", "fwd_inv", f"Sarvam Vision could not read {filename} ({e}); using sample fields")
        ob = scenario.add_forwarder_invoice(self.L, fields, source)
        self.L.audit(
            "invoice",
            ob.id,
            f"{fields.get('vendor_name')} invoice {ob.invoice_no}: {inr(ob.amount)} against quote {inr(ob.quoted or 0)}"
            + (f"; {inr(ob.disputed)} above quote" if ob.disputed else ""),
            {"source": source, "line_items": ob.line_items, "confidence": ob.extraction_confidence},
        )
        # Tier 1: what kind of charge is the gap?
        if ob.disputed:
            try:
                kind = await self.engine.classify(
                    {"invoice_lines": ob.line_items, "quoted": ob.quoted, "total": ob.amount},
                    f"What kind of charge is the {ob.disputed} above the quote?",
                    {"detention": "detention", "documentation": "documentation", "storage": "storage", "unexplained": "unexplained"},
                )
                self.L.audit("prism", ob.id, f"Prism: the gap looks like {kind['choice']} (confidence {kind['confidence']:.2f})", kind)
            except SarvamError as e:
                self.L.audit("prism", ob.id, f"Prism unavailable: {e}")
        self.L.changed()
        return {"obligation": ob.id, "options": [o.model_dump() for o in tier0.options(self.L)]}

    async def call_forwarder(self, language: str | None = None) -> Call:
        ob = self.L.obligations["fwd_inv"]
        if language:
            self.L.parties["fwd"].language = language
        return await self.calls.start(
            "forwarder_release",
            "fwd",
            {"obligation_id": ob.id, "invoice": ob.amount, "quoted": ob.quoted, "gap": ob.disputed},
        )

    # ------------------------------------------------------------------
    # 2. driver voice note asking for a different account
    # ------------------------------------------------------------------
    async def driver_note(self, text: str | None = None, audio: bytes | None = None, filename: str = "note.webm") -> dict[str, Any]:
        original, english = text or "", text or ""
        if audio:
            heard = await self.sarvam.stt_file(audio, filename, mode="transcribe")
            original = heard["transcript"]
            english = await self.sarvam.translate(original, heard.get("language") or "ta-IN", "en-IN")
        elif text and not text.isascii():
            english = await self.sarvam.translate(text, "ta-IN", "en-IN")
        intent = await self.engine.classify(
            english,
            "What is the driver asking for?",
            {"delivered": "goods delivered", "waiting": "still waiting", "expense": "an expense", "account_change": "pay into a different account", "other": "something else"},
        )
        self.L.audit("voice_note", "truck_bal", f"Driver voice note: \"{english}\" -> Prism: {intent['choice']} ({intent['confidence']:.2f})", {"original": original, "intent": intent})
        result: dict[str, Any] = {"intent": intent, "english": english}
        if intent["choice"] == "account_change":
            acc = await self._extract_account(english)
            if acc is None:
                acc = scenario.BROTHER_ACC  # demo note always asks for the brother's account
            payout = await self.payments.request("truck_bal", self.L.obligations["truck_bal"].outstanding, "driver voice note", acc)
            result["payout"] = payout.id
        self.L.changed()
        return result

    async def _extract_account(self, english: str) -> Account | None:
        try:
            out = await self.sarvam.chat_json(
                [
                    {
                        "role": "user",
                        "content": "Extract the bank account the speaker wants money sent to. JSON only: "
                        '{"account_number": digits or null, "ifsc": string or null, "holder": string or null}. '
                        f"Do not guess missing values.\n\nText: {english}",
                    }
                ],
                model=self.cfg.model_extract,
                max_tokens=600,
            )
        except Exception:  # noqa: BLE001
            return None
        num = re.sub(r"\D", "", str(out.get("account_number") or ""))
        ifsc = str(out.get("ifsc") or "").upper()
        if len(num) < 6 or not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", ifsc):
            return None
        return Account(number=num, ifsc=ifsc, holder=str(out.get("holder") or "Unknown"))

    async def _needs_call(self, payout, decision: Decision) -> None:
        failing = next((r for r in decision.rules if not r.ok and r.blocking == "call"), None)
        if failing and failing.rule == "account_change_confirmed":
            party = self.L.parties[payout.party_id]
            await self.calls.start(
                "account_change",
                party.id,
                {
                    "payout_id": payout.id,
                    "amount": payout.amount,
                    "new_last4": payout.to_account.last4,
                    "old_last4": party.account.last4 if party.account else "",
                },
            )

    # ------------------------------------------------------------------
    # 3. buyer call with a payment link
    # ------------------------------------------------------------------
    async def call_buyer(self, language: str | None = None) -> Call:
        c = self.L.collections["buyer_po"]
        if language:
            self.L.parties["buyer"].language = language
        return await self.calls.start("buyer_collection", "buyer", {"collection_id": c.id, "amount": c.amount - c.received, "label": c.label})

    async def _make_link(self, call: Call, amount: int) -> str:
        c = self.L.collections[call.about.get("collection_id", "buyer_po")]
        party = self.L.parties[c.party_id]
        link_id = f"tf_{c.id}_{self.L.new_id('l')}"
        link = await self.rails.create_link(link_id, min(amount, c.amount - c.received), party.name, party.phone, c.label, min_partial=1)
        c.link_id, c.link_url = link.link_id, link.link_url
        self.L.audit("link", c.id, f"Payment link for {inr(amount)} created during call {call.id}, part payment allowed", {"url": link.link_url})
        self.L.emit("link", {"collection": c.id, "url": link.link_url, "amount": amount, "call_id": call.id})
        self.L.changed()
        return link.link_url

    # ------------------------------------------------------------------
    # call outcomes -> facts -> decisions
    # ------------------------------------------------------------------
    async def _call_finished(self, call: Call) -> None:
        f = call.findings
        if call.purpose == "forwarder_release":
            agreed = f.get("release_agreed")
            if agreed is None:
                self.L.audit("uncertain", call.id, "Could not tell if the forwarder agreed; the owner will be asked")
            self.L.learn("forwarder_release", agreed, f"call:{call.id}", f.get("p_release"))
            if f.get("charge"):
                self.L.learn("forwarder_charge", f["charge"], f"call:{call.id}", f.get("charge_confidence"))
            if agreed:
                top = tier0.options(self.L)[0]
                line = next((ln for ln in top.pays if ln.obligation_id == "fwd_inv"), None)
                if top.key == "B" and line:
                    self.L.audit("plan", "fwd_inv", f"Option B moves to the top: pay {inr(line.amount)} now, {inr(top.cash_left)} left")
                    await self.payments.request("fwd_inv", line.amount, "option B after forwarder call")
        elif call.purpose == "account_change":
            payout = self.L.payouts[call.about["payout_id"]]
            key = f"account_change_confirmed:{payout.party_id}:{payout.to_account.last4}"
            confirmed = f.get("confirmed")
            if confirmed is None:
                self.L.audit("uncertain", payout.id, "The confirmation call was unclear; refusing to be safe")
                confirmed = False
            self.L.learn(key, confirmed, f"call:{call.id}", f.get("p_confirmed"))
            if payout.status == PayoutStatus.NEEDS_CALL:
                await self.payments.run(payout)
        elif call.purpose == "buyer_collection":
            c = self.L.collections[call.about["collection_id"]]
            if f.get("commitment"):
                c.commitment = f["commitment"]
                self.L.audit("collection", c.id, f"Buyer commitment on call: {c.commitment.replace('_', ' ')}")
        self.L.changed()

    async def finish_call(self, call_id: str, typed: list[dict[str, str]] | None = None) -> Call:
        call = self.L.calls[call_id]
        transcript = None
        if typed:
            transcript = [Turn(role=t.get("role", "user"), text=t["text"]) for t in typed]
        return await self.calls.finish(call, transcript)

    # ------------------------------------------------------------------
    # webhooks (real ones arrive over HTTP; simulated ones are delivered in-process)
    # ------------------------------------------------------------------
    async def webhook(self, product: str, payload: dict[str, Any]) -> None:
        if product == "payouts":
            self.payments.payout_webhook(payload)
            return
        if product == "links":
            d = payload.get("data") or {}
            c = next((c for c in self.L.collections.values() if c.link_id == d.get("link_id")), None)
            if c is None:
                return
            paid_total = int(float(d.get("link_amount_paid") or 0))
            delta = paid_total - c.received
            if delta > 0:
                c.received = paid_total
                self.L.balance += delta
                self.L.audit(
                    "collected",
                    c.id,
                    f"Buyer paid {inr(delta)} by link ({d.get('link_status')})" + (" (simulated)" if d.get("simulated") else ""),
                    {"status": d.get("link_status")},
                )
                self.L.emit("collected", {"collection": c.id, "amount": delta})
                self.L.changed()


def _party_view(p) -> dict[str, Any]:
    d = p.model_dump(mode="json")
    if p.account:
        d["account"] = {"last4": p.account.last4, "ifsc": p.account.ifsc, "masked": mask(p.account.number), "holder": p.account.holder}
    d["phone"] = mask(p.phone)
    return d


def _payout_view(p) -> dict[str, Any]:
    d = p.model_dump(mode="json")
    d["to_account"] = {"masked": mask(p.to_account.number), "ifsc": p.to_account.ifsc, "holder": p.to_account.holder}
    return d
