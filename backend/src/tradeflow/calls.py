"""CallDesk: when one unknown decides the outcome, phone the right person and find out.

A call has a purpose. The purpose sets what the agent asks, which Prism questions read
the transcript afterwards, and which facts it can teach the engine.

Two channels sit behind the same interface:
  * browser - the other person talks to our own voice loop (Saaras -> Sarvam 105B -> Bulbul)
              from a phone browser; used when no telephony is configured, and for judges.
  * phone   - Sarvam Voice Agents instant outbound call; the transcript comes back on the
              agent's on-end API hook.
Either way, finish() is the single place a transcript turns into facts.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx

from .config import Settings
from .domain import Call, Party, Turn, now
from .engine import Engine
from .ledger import Ledger
from .money import inr, mask
from .sarvam import Sarvam
from .speaker import Speaker, Utterance

log = logging.getLogger(__name__)

Finished = Callable[[Call], Awaitable[None]]
LinkMaker = Callable[[Call, int], Awaitable[str]]

YES, NO = 0.8, 0.2  # Prism probability thresholds; in between is "uncertain"


@dataclass
class Purpose:
    goal: str  # what the agent must find out (English, for the LLM)
    opening: str  # first line, English, translated before speaking


def _purposes(call: Call, party: Party, L: Ledger) -> Purpose:
    a = call.about
    owner = L.merchant["owner"]
    biz = L.merchant["name"]
    notice = "This call is recorded. "
    if call.purpose == "forwarder_release":
        return Purpose(
            goal=(
                f"You are calling {party.name}, the freight forwarder, for {owner} of {biz}. Their invoice is "
                f"{inr(a['invoice'])} but the agreed quote was {inr(a['quoted'])}, so {inr(a['gap'])} is extra. "
                f"Find out: (1) what the {inr(a['gap'])} is for, and (2) whether they will release the cargo today "
                f"if {owner} pays {inr(a['quoted'])} now, with the rest settled after proof. Do not promise anything else."
            ),
            opening=f"{notice}Hello, I am calling from {biz} for {owner}. Your invoice is {inr(a['gap'])} above the quote. What is that amount for?",
        )
    if call.purpose == "account_change":
        return Purpose(
            goal=(
                f"You are calling {party.name} on the number saved at onboarding, for {owner} of {biz}. "
                f"Someone asked to send {party.name}'s payment of {inr(a['amount'])} to a different bank account "
                f"ending {a['new_last4']} instead of the saved account ending {a['old_last4']}. Ask only whether "
                f"{party.name} personally asked for this change. Never read out full account numbers."
            ),
            opening=(
                f"{notice}Hello {party.name}, calling from {biz}. We got a request to send your payment of "
                f"{inr(a['amount'])} to a new account ending {a['new_last4']}. Did you ask for this change?"
            ),
        )
    if call.purpose == "buyer_collection":
        return Purpose(
            goal=(
                f"You are calling the accounts team of {party.name}, a buyer of {biz}. {inr(a['amount'])} is due "
                f"({a['label']}). Politely confirm the payment date. If they can pay now, even partly, offer a payment "
                "link: output the tag <<LINK:amount>> with the amount in rupees as digits, and say the link has been sent. "
                "Never threaten; part payment is fine."
            ),
            opening=f"{notice}Hello, calling from {biz} about {a['label']} of {inr(a['amount'])}. Could you confirm when it will be paid?",
        )
    if call.purpose == "driver_photo":
        return Purpose(
            goal=f"You are calling {party.name}, a truck driver. The delivery receipt photo is missing or blurry. Ask him to send a fresh, clear photo on WhatsApp.",
            opening=f"{notice}Hello, calling from {biz}. The delivery receipt photo is not clear. Please send a fresh photo on WhatsApp.",
        )
    raise ValueError(f"unknown purpose {call.purpose}")


class CallDesk:
    def __init__(self, ledger: Ledger, sarvam: Sarvam, engine: Engine, speaker: Speaker, cfg: Settings) -> None:
        self.L = ledger
        self.sarvam = sarvam
        self.engine = engine
        self.speaker = speaker
        self.cfg = cfg
        self.on_finished: Finished | None = None
        self.make_link: LinkMaker | None = None

    # ------------------------------------------------------------------
    async def start(self, purpose: str, party_id: str, about: dict[str, Any], channel: str | None = None) -> Call:
        party = self.L.parties[party_id]
        hour = now().hour
        lo, hi = self.cfg.call_hours
        if self.cfg.enforce_call_hours and not lo <= hour < hi:
            raise ValueError(f"Calls only between {lo}:00 and {hi}:00 IST")
        ch = channel or ("phone" if self.cfg.sarvam_phone_calls else "browser")
        call = Call(id=self.L.new_id("call"), purpose=purpose, party_id=party.id, language=party.language, channel=ch, about=about)
        self.L.calls[call.id] = call
        self.L.audit(
            "call",
            call.id,
            f"Calling {party.name} ({party.role.value}, {mask(party.phone)}) in {party.language}: {purpose.replace('_', ' ')}",
            {"about": about, "channel": ch, "ref": about.get("payout_id")},
        )
        if about.get("payout_id"):
            self.L.audit("call", about["payout_id"], f"Call {call.id} placed to {party.name} ({purpose.replace('_', ' ')})")
        if ch == "phone":
            try:
                await self._dial(party)
            except Exception as e:  # noqa: BLE001
                self.L.audit("call", call.id, f"Phone call failed ({e}); falling back to browser link")
                call.channel = "browser"
        self.L.emit("ring", {"call_id": call.id, "role": party.role.value, "party": party.name})
        self.L.changed()
        return call

    async def _dial(self, party: Party) -> None:
        c = self.cfg
        url = f"https://apps.sarvam.ai/api/outbounds/v1/orgs/{c.sarvam_org_id}/workspaces/{c.sarvam_workspace_id}/outbounds"
        body = {
            "app_config": {
                "app_id": c.sarvam_app_id,
                "app_version": c.sarvam_app_version,
                "connection_config": {"connection_id": c.sarvam_connection_id, "agent_phone_number": c.sarvam_agent_phone},
            },
            "user_config": {"user_phone_number": party.phone},
        }
        async with httpx.AsyncClient(timeout=20) as h:
            r = await h.post(url, json=body, headers={"X-API-Key": c.sarvam_agents_api_key})
            r.raise_for_status()

    # ------------------------------------------------------------------
    # browser channel: our own agent turns
    # ------------------------------------------------------------------
    async def greet(self, call: Call) -> Utterance:
        party = self.L.parties[call.party_id]
        call.status = "live"
        p = _purposes(call, party, self.L)
        u = await self.speaker.say(p.opening, call.language)
        call.transcript.append(Turn(role="agent", text=u.text, en=p.opening))
        self.L.changed()
        return u

    async def reply(self, call: Call, user_text: str) -> tuple[Utterance, bool, list[str]]:
        """Returns (what the agent says, whether the call should end, notes for the UI)."""
        party = self.L.parties[call.party_id]
        call.transcript.append(Turn(role="user", text=user_text))
        p = _purposes(call, party, self.L)
        system = (
            f"{p.goal}\nSpeak only in the language with code {call.language}, in the everyday mixed style people "
            "use on the phone; English words for money and shipping are fine. Keep each reply under 30 words. Be "
            "polite and never threaten. When you have the answer you need, thank them and add the tag <<END>>."
        )
        msgs = [{"role": "system", "content": system}]
        for t in call.transcript:
            msgs.append({"role": "assistant" if t.role == "agent" else "user", "content": t.text})
        notes: list[str] = []
        try:
            text = await self.sarvam.chat(msgs, model=self.cfg.model_conversation, max_tokens=900, reasoning_effort=None)
        except Exception as e:  # noqa: BLE001
            log.warning("agent turn failed: %s", e)
            text = "Sorry, the line is unclear. I will call back. <<END>>"
        ended = "<<END>>" in text
        for m in re.finditer(r"<<LINK:\s*([\d,]+)\s*>>", text):
            amount = int(m.group(1).replace(",", ""))
            if self.make_link:
                url = await self.make_link(call, amount)
                notes.append(f"Payment link for {inr(amount)} sent: {url}")
        spoken = re.sub(r"<<[^>]*>>", "", text).strip()
        audio = await self.speaker.voice(spoken, call.language) if spoken else None
        call.transcript.append(Turn(role="agent", text=spoken))
        self.L.changed()
        return Utterance(en="", text=spoken, language=call.language, audio_id=audio), ended, notes

    # ------------------------------------------------------------------
    # phone channel: Sarvam on-end hook
    # ------------------------------------------------------------------
    def call_for_phone(self, phone: str) -> Call | None:
        digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
        for call in sorted(self.L.calls.values(), key=lambda c: c.created, reverse=True):
            party = self.L.parties[call.party_id]
            if call.status != "done" and "".join(ch for ch in party.phone if ch.isdigit())[-10:] == digits:
                return call
        return None

    # ------------------------------------------------------------------
    async def finish(self, call: Call, transcript: list[Turn] | None = None) -> Call:
        """The single place a transcript turns into facts."""
        if call.status == "done":
            return call
        if transcript is not None:
            call.transcript = transcript
        call.status = "done"
        for t in call.transcript:
            if t.en is None:
                try:
                    t.en = await self.sarvam.translate(t.text, call.language, "en-IN") if call.language != "en-IN" else t.text
                except Exception:  # noqa: BLE001
                    t.en = t.text
        english = "\n".join(f"{'Agent' if t.role == 'agent' else 'Them'}: {t.en}" for t in call.transcript)
        self.L.audit("transcript", call.id, f"Call ended with {len(call.transcript)} turns", {"en": english, "original": [t.model_dump(mode='json') for t in call.transcript]})
        findings = await self._read(call, english)
        call.findings = findings
        self.L.audit("findings", call.id, f"Prism read the call: {findings}", findings)
        if call.about.get("payout_id"):
            self.L.audit("findings", call.about["payout_id"], f"From call {call.id}: {findings}", findings)
        self.L.changed()
        if self.on_finished:
            await self.on_finished(call)
        return call

    async def _read(self, call: Call, english: str) -> dict[str, Any]:
        party = self.L.parties[call.party_id]
        a = call.about
        out: dict[str, Any] = {}
        try:
            if call.purpose == "forwarder_release":
                state = {"context": f"Exporter asked whether cargo is released if he pays {a['quoted']} now instead of the full {a['invoice']}.", "call": english}
                p = await self.engine.yes_no(state, f"Did the forwarder agree to release the cargo after a payment of {a['quoted']}?")
                kind = await self.engine.classify(
                    state,
                    f"What is the extra {a['gap']} on the invoice for, according to the forwarder?",
                    {"detention": "detention", "documentation": "documentation", "storage": "storage", "unexplained": "no reason given"},
                )
                out = {"release_agreed": _tri(p), "p_release": round(p, 3), "charge": kind["choice"], "charge_confidence": round(kind["confidence"], 3)}
            elif call.purpose == "account_change":
                state = {"context": f"Someone asked to pay {party.name} into a new account. The agent called {party.name} on the saved number to check.", "call": english}
                p = await self.engine.yes_no(state, f"Did {party.name} confirm that he himself asked to change the bank account?")
                out = {"confirmed": _tri(p), "p_confirmed": round(p, 3)}
            elif call.purpose == "buyer_collection":
                state = {"context": f"Agent called the buyer about {a['amount']} due.", "call": english}
                s = await self.engine.score(state, "How firm is the buyer's commitment to pay?", ["refused", "vague", "soft date", "firm date", "paid on call"])
                level = s["legend"][str(round(s["score"]))]
                out = {"commitment": level.replace(" ", "_"), "commitment_score": round(s["score"], 2), "confidence": round(s["confidence"], 3)}
        except Exception as e:  # noqa: BLE001
            out = {"error": f"Prism could not read the call: {e}"}
        return out


def _tri(p: float) -> bool | None:
    if p >= YES:
        return True
    if p <= NO:
        return False
    return None
