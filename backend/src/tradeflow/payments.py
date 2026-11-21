"""PaymentDesk: the only module that moves money.

    request()  -> rules + engine -> refuse | call | auto-pay | ask the owner
    respond()  -> the owner's nod/shake (or screen tap), with a one-time token
    webhook()  -> Cashfree's word on what happened. The only source of payout status.

Invariants:
  * One fixed transfer_id per obligation attempt. A retry reuses it, so it can never pay twice.
  * A reversal reopens the obligation and bumps its attempt; a new approval is required.
  * An unknown outcome (timeout) is polled, never retried.
  * A nod counts only while the approval is armed (within the window after the brief ends),
    only for that approval's token, and only with AirPods in.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import timedelta
from typing import Any, Awaitable, Callable

import httpx

from .config import Settings
from .domain import (
    Account,
    Approval,
    ObligationStatus,
    Payout,
    PayoutStatus,
    now,
)
from .engine import Decision, Engine
from .ledger import Ledger
from .money import inr, mask
from .rails import Rails, RailsError
from .speaker import Speaker

log = logging.getLogger(__name__)

NeedsCall = Callable[[Payout, Decision], Awaitable[None]]


class ApprovalError(ValueError):
    pass


class PaymentDesk:
    def __init__(self, ledger: Ledger, engine: Engine, rails: Rails, speaker: Speaker, cfg: Settings) -> None:
        self.L = ledger
        self.engine = engine
        self.rails = rails
        self.speaker = speaker
        self.cfg = cfg
        self.on_needs_call: NeedsCall | None = None
        self.brief_audio: dict[str, str | None] = {}  # approval id -> audio id
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # request
    # ------------------------------------------------------------------
    async def request(
        self, obligation_id: str, amount: int, requested_by: str, to_account: Account | None = None
    ) -> Payout:
        ob = self.L.obligations[obligation_id]
        party = self.L.parties[ob.party_id]
        account = to_account or party.account
        if account is None:
            raise ValueError(f"{party.name} has no account on file")
        payout = Payout(
            id=self.L.new_id("po"),
            obligation_id=ob.id,
            party_id=party.id,
            amount=amount,
            to_account=account,
            requested_by=requested_by,
            status=PayoutStatus.CHECKING,
            simulated=self.rails.simulated,
        )
        self.L.payouts[payout.id] = payout
        self.L.audit(
            "request",
            payout.id,
            f"{inr(amount)} to {party.name} ({mask(account.number)}) for {ob.label}, requested by {requested_by}",
            {"obligation": ob.id},
        )
        self.L.changed()
        await self.run(payout)
        return payout

    async def run(self, payout: Payout) -> Decision:
        """(Re)evaluate a payout. Called again after a call brings in new facts."""
        # A new account must itself pass Secure ID before anything else.
        party = self.L.parties[payout.party_id]
        if party.account and not party.account.same_as(payout.to_account):
            v = await self.rails.verify_account(payout.to_account, party.phone)
            self.L.account_checks[payout.to_account.number] = v
            self.L.audit(
                "verify",
                payout.id,
                f"Secure ID on new account {mask(payout.to_account.number)}: {v.account_status} / {v.name_match}"
                + (f" (name at bank: {v.name_at_bank})" if v.name_at_bank else ""),
                v.model_dump(),
            )
        decision = await self.engine.decide(payout)
        self.L.audit("decision", payout.id, f"Route: {decision.route}. {decision.reason}", decision.model_dump(exclude={"rules"}))
        if decision.route == "refuse":
            await self.refuse(payout, decision.reason)
        elif decision.route == "call":
            payout.status = PayoutStatus.NEEDS_CALL
            self.L.changed()
            if self.on_needs_call:
                await self.on_needs_call(payout, decision)
        elif decision.route == "auto":
            self.L.audit("approve", payout.id, "Auto-approved: small, verified, routine")
            await self.execute(payout)
        else:
            await self._ask_owner(payout, decision)
        return decision

    async def refuse(self, payout: Payout, reason_en: str) -> None:
        payout.status = PayoutStatus.REFUSED
        payout.reason = reason_en
        party = self.L.parties[payout.party_id]
        spoken = f"I have stopped the {inr(payout.amount)} payment to {party.name}. Reason: {reason_en}. No money moved."
        u = await self.speaker.say(spoken, self.cfg.owner_language)
        payout.reason_spoken = u.text
        self.L.audit("refuse", payout.id, f"Refused: {reason_en}", {"spoken": u.text, "language": u.language})
        self.L.emit("say", {"ref": payout.id, "text": u.text, "en": u.en, "audio_id": u.audio_id, "tone": "refuse"})
        self.L.changed()

    # ------------------------------------------------------------------
    # approvals
    # ------------------------------------------------------------------
    async def _ask_owner(self, payout: Payout, d: Decision) -> Approval:
        for a in self.L.approvals.values():
            if a.payout_id == payout.id and a.state in {"pending", "armed"}:
                a.state = "superseded"
        brief = await self.speaker.say(d.brief_en, self.cfg.owner_language)
        approval = Approval(
            id=self.L.new_id("ap"),
            payout_id=payout.id,
            amount=payout.amount,
            option_key=d.option_key,
            brief_en=d.brief_en,
            brief=brief.text,
            explain_en=d.explain_en,
            token=secrets.token_urlsafe(16),
            pin_required=payout.amount > self.cfg.pin_limit,
        )
        self.L.approvals[approval.id] = approval
        self.brief_audio[approval.id] = brief.audio_id
        payout.status = PayoutStatus.AWAITING_OWNER
        self.L.audit("brief", payout.id, f"Asked {self.L.merchant['owner']}: {d.brief_en}", {"spoken": brief.text, "approval": approval.id})
        self.L.emit("approval", {"id": approval.id, "audio_id": brief.audio_id})
        self.L.changed()
        return approval

    def arm(self, approval_id: str) -> Approval:
        """The brief finished playing; the nod window starts now."""
        a = self._approval(approval_id)
        if a.state not in {"pending", "armed"}:
            raise ApprovalError(f"approval is {a.state}")
        a.state = "armed"
        a.armed_at = now()
        self.L.audit("arm", a.payout_id, f"Brief played; {self.cfg.approval_window_s:.0f}s window open")
        self.L.changed()
        return a

    async def respond(
        self,
        approval_id: str,
        token: str,
        gesture: str,  # nod | shake | approve | refuse
        source: str,  # airpods | screen
        ears_in: bool | None = None,
        pin: str | None = None,
    ) -> Approval:
        a = self._approval(approval_id)
        payout = self.L.payouts[a.payout_id]
        if not secrets.compare_digest(token, a.token):
            raise ApprovalError("token does not match this approval")
        if a.state not in {"pending", "armed"}:
            raise ApprovalError(f"approval already {a.state}")
        if source == "airpods":
            if a.state != "armed" or a.armed_at is None:
                raise ApprovalError("brief has not finished playing")
            if now() - a.armed_at > timedelta(seconds=self.cfg.approval_window_s):
                a.state = "expired"
                self.L.audit("expire", a.payout_id, "Nod window passed; nothing approved")
                self.L.changed()
                raise ApprovalError("window expired; replay the brief")
            if not ears_in:
                raise ApprovalError("AirPods not in ears; nothing can be approved")
        approve = gesture in {"nod", "approve"}
        if approve and a.pin_required and pin != self.cfg.owner_pin:
            raise ApprovalError("PIN required above the limit")

        a.token = ""  # one-time
        a.decided_at = now()
        a.decided_by = f"{source}:{gesture}"
        a.ears_in = ears_in
        a.state = "approved" if approve else "refused"
        self.L.audit(
            "owner",
            a.payout_id,
            f"{self.L.merchant['owner']} {'approved' if approve else 'refused'} by {source} {gesture}",
            {"approval": a.id, "ears_in": ears_in, "at": a.decided_at.isoformat()},
        )
        if approve:
            await self.execute(payout)
        else:
            await self.refuse(payout, "The owner refused")
        self.L.changed()
        return a

    async def explain(self, approval_id: str) -> dict[str, Any]:
        a = self._approval(approval_id)
        text = "Three factors. " + " ".join(f"{i + 1}: {r}." for i, r in enumerate(a.explain_en[:3]))
        u = await self.speaker.say(text, self.cfg.owner_language)
        if a.state == "armed":
            a.state = "pending"  # re-arm after the explanation finishes playing
        self.L.audit("explain", a.payout_id, "Owner tilted for an explanation", {"spoken": u.text})
        return {"text": u.text, "en": u.en, "audio_id": u.audio_id}

    async def amend(self, approval_id: str, new_amount: int) -> Approval:
        """Owner spoke a change, e.g. 'only pay 30,000'. Amounts can only go down."""
        a = self._approval(approval_id)
        payout = self.L.payouts[a.payout_id]
        if not 0 < new_amount < payout.amount:
            raise ApprovalError(f"new amount {inr(new_amount)} must be below {inr(payout.amount)}")
        a.state = "superseded"
        self.L.audit("amend", payout.id, f"Owner changed amount {inr(payout.amount)} -> {inr(new_amount)}")
        payout.status = PayoutStatus.REFUSED
        payout.reason = f"Replaced by owner's amended amount {inr(new_amount)}"
        new = await self.request(payout.obligation_id, new_amount, f"owner amendment of {payout.id}", payout.to_account)
        latest = [x for x in self.L.approvals.values() if x.payout_id == new.id]
        if not latest:
            raise ApprovalError(f"amended payout went to {new.status}")
        return latest[-1]

    def _approval(self, approval_id: str) -> Approval:
        a = self.L.approvals.get(approval_id)
        if a is None:
            raise ApprovalError("no such approval")
        return a

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    async def execute(self, payout: Payout) -> None:
        ob = self.L.obligations[payout.obligation_id]
        party = self.L.parties[payout.party_id]
        payout.transfer_id = ob.transfer_id() if payout.amount == ob.outstanding else f"{ob.transfer_id()}_p{ob.paid + payout.amount}"
        try:
            bene = await self.rails.ensure_beneficiary(party, payout.to_account)
            st = await self.rails.pay(payout.transfer_id, bene, payout.amount, f"TradeFlow {ob.label}")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            payout.status = PayoutStatus.UNKNOWN
            ob.status = ObligationStatus.IN_FLIGHT
            self.L.audit("payout", payout.id, f"No answer from Cashfree ({e}); status unknown, polling, not retrying")
            self._start_poll(payout)
            self.L.changed()
            return
        except RailsError as e:
            payout.status = PayoutStatus.FAILED
            payout.reason = str(e)
            self.L.audit("payout", payout.id, f"Cashfree rejected the request: {e}")
            self.L.changed()
            return
        payout.status = PayoutStatus.SUBMITTED
        payout.cf_transfer_id = st.cf_transfer_id
        payout.status_code = st.status_code
        ob.status = ObligationStatus.IN_FLIGHT
        self.L.audit(
            "payout",
            payout.id,
            f"Submitted to Cashfree: transfer_id {payout.transfer_id}, cf {st.cf_transfer_id}, {st.status}"
            + (" (simulated rails)" if self.rails.simulated else ""),
            {"transfer_id": payout.transfer_id, "status": st.status},
        )
        self._start_poll(payout)
        self.L.changed()

    def _start_poll(self, payout: Payout) -> None:
        if payout.id in self._poll_tasks:
            return

        async def poll() -> None:
            await asyncio.sleep(self.cfg.poll_fallback_s)
            for _ in range(20):
                if payout.status not in {PayoutStatus.SUBMITTED, PayoutStatus.SENT, PayoutStatus.UNKNOWN}:
                    return
                try:
                    st = await self.rails.transfer_status(payout.transfer_id or "")
                    self._apply(payout, st.status, st.status_code, "poll")
                except Exception as e:  # noqa: BLE001
                    log.info("poll %s: %s", payout.id, e)
                await asyncio.sleep(self.cfg.poll_fallback_s)

        self._poll_tasks[payout.id] = asyncio.create_task(poll())

    # ------------------------------------------------------------------
    # webhooks
    # ------------------------------------------------------------------
    def payout_webhook(self, payload: dict[str, Any]) -> None:
        data = payload.get("data") or {}
        tid = data.get("transfer_id")
        payout = next((p for p in self.L.payouts.values() if p.transfer_id == tid), None)
        if payout is None:
            log.info("webhook for unknown transfer %s", tid)
            return
        self._apply(payout, data.get("status", ""), data.get("status_code"), f"webhook {payload.get('type')}")

    def _apply(self, payout: Payout, status: str, code: str | None, via: str) -> None:
        ob = self.L.obligations[payout.obligation_id]
        before = payout.status
        status = (status or "").upper()
        if status == "SUCCESS" and code == "COMPLETED" or status == "ACKNOWLEDGED":
            new = PayoutStatus.PAID
        elif status == "SUCCESS":
            new = PayoutStatus.SENT
        elif status in {"FAILED", "REJECTED"}:
            new = PayoutStatus.FAILED
        elif status == "REVERSED":
            new = PayoutStatus.REVERSED
        else:
            return  # RECEIVED / PENDING: nothing final yet
        if new == before:
            return
        payout.status = new
        payout.status_code = code
        if new == PayoutStatus.PAID and before != PayoutStatus.PAID:
            ob.paid += payout.amount
            self.L.balance -= payout.amount
            self.L.parties[payout.party_id].paid_before = True
            ob.status = ObligationStatus.PAID if ob.outstanding <= 0 else ObligationStatus.OPEN
        elif new == PayoutStatus.FAILED:
            ob.status = ObligationStatus.OPEN
        elif new == PayoutStatus.REVERSED:
            if before == PayoutStatus.PAID:
                ob.paid -= payout.amount
                self.L.balance += payout.amount
            ob.attempt += 1  # a fresh transfer_id, but only after a fresh approval
            ob.status = ObligationStatus.OPEN
        self.L.audit(
            "status",
            payout.id,
            f"{via}: {status}/{code} -> {new.value}" + (" (simulated)" if self.rails.simulated else ""),
            {"status": status, "status_code": code},
        )
        self.L.emit("payout", {"id": payout.id, "status": new.value})
        self.L.changed()
