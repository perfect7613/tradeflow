"""Domain types. Names follow CONTEXT.md. Amounts are whole rupees (int)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

IST = timezone(timedelta(hours=5, minutes=30))


def now() -> datetime:
    return datetime.now(IST)


class Role(StrEnum):
    TRUCK_OWNER = "truck_owner"
    DRIVER = "driver"
    FORWARDER = "forwarder"
    SUPPLIER = "supplier"
    BUYER = "buyer"


class Account(BaseModel):
    number: str
    ifsc: str
    holder: str

    @property
    def last4(self) -> str:
        return self.number[-4:]

    def same_as(self, other: Account | None) -> bool:
        return other is not None and self.number == other.number and self.ifsc == other.ifsc


class Verification(BaseModel):
    """Result of a Secure ID bank account check."""

    account_status: str  # VALID | INVALID
    name_match: str  # DIRECT_MATCH | GOOD_PARTIAL_MATCH | MODERATE_PARTIAL_MATCH | POOR_PARTIAL_MATCH | NO_MATCH
    name_at_bank: str = ""
    reference_id: str = ""
    simulated: bool = False

    @property
    def passes(self) -> bool:
        return self.account_status == "VALID" and self.name_match in {"DIRECT_MATCH", "GOOD_PARTIAL_MATCH"}


class Party(BaseModel):
    id: str
    name: str
    role: Role
    phone: str  # the number saved at onboarding; confirmation calls go only here
    language: str  # BCP-47, e.g. ta-IN
    account: Account | None = None
    verification: Verification | None = None
    beneficiary_id: str | None = None
    udyam: str | None = None
    paid_before: bool = False
    pays_late: tuple[int, int] | None = None  # buyers only: (late, out of)


class ObligationStatus(StrEnum):
    OPEN = "open"
    IN_FLIGHT = "in_flight"  # a payout exists and has not reached a final state
    PAID = "paid"
    HELD = "held"


class Obligation(BaseModel):
    id: str
    party_id: str
    kind: str  # truck_advance | truck_balance | freight | storage | supplier
    label: str
    amount: int
    quoted: int | None = None  # what was agreed before the invoice arrived
    paid: int = 0
    due: datetime
    invoice_no: str | None = None
    line_items: list[tuple[str, int]] = Field(default_factory=list)
    tax: int = 0
    extraction_confidence: float = 1.0
    status: ObligationStatus = ObligationStatus.OPEN
    attempt: int = 1  # bumps only when a reversal reopens the obligation
    msme_protected: bool = False  # supplier is Udyam-registered: 45-day rule applies
    source: str = "seed"  # where it came from: seed | upload | voice_note

    @property
    def outstanding(self) -> int:
        return self.amount - self.paid

    @property
    def disputed(self) -> int:
        return max(0, self.amount - self.quoted) if self.quoted is not None else 0

    def transfer_id(self) -> str:
        """Fixed per obligation and attempt. Retrying the same attempt reuses the same ID,
        so Cashfree rejects a duplicate instead of paying twice."""
        return f"tf_{self.id}_{self.attempt}"


class Collection(BaseModel):
    id: str
    party_id: str
    label: str
    amount: int
    expected: datetime
    received: int = 0
    link_id: str | None = None
    link_url: str | None = None
    commitment: str | None = None  # from the buyer call: refused | vague | soft_date | firm_date | paid_on_call


class Money(BaseModel):
    """One line of the cash position. `kind` tells the UI how to label it."""

    label: str
    amount: int
    kind: str  # confirmed | pending | estimated | simulated


class CashPosition(BaseModel):
    confirmed: int
    pending_out: int
    expected_in_weighted: int
    due_7d: int
    lines: list[Money]

    @property
    def spendable(self) -> int:
        return self.confirmed - self.pending_out


class PlanLine(BaseModel):
    obligation_id: str
    label: str
    amount: int


class Option(BaseModel):
    key: str  # A | B | C
    title: str
    pays: list[PlanLine]
    pays_now: int
    cash_left: int
    catch: str
    feasible: bool
    needs: str | None = None  # a fact still unknown, e.g. "forwarder_release"
    score: float = 0.0


class RuleResult(BaseModel):
    rule: str
    ok: bool
    detail: str
    blocking: str = "refuse"  # refuse | call | ask_owner (what happens if not ok)


class PayoutStatus(StrEnum):
    DRAFT = "draft"
    CHECKING = "checking"
    NEEDS_CALL = "needs_call"
    AWAITING_OWNER = "awaiting_owner"
    REFUSED = "refused"
    SUBMITTED = "submitted"  # sent to Cashfree, waiting for the webhook
    SENT = "sent"  # Cashfree SUCCESS but not yet COMPLETED at the beneficiary bank
    PAID = "paid"  # SUCCESS + COMPLETED, or ACKNOWLEDGED
    FAILED = "failed"
    REVERSED = "reversed"
    UNKNOWN = "unknown"  # we could not learn the state; never retried blindly


class Payout(BaseModel):
    id: str
    obligation_id: str
    party_id: str
    amount: int
    to_account: Account
    requested_by: str  # e.g. "invoice", "driver voice note"
    status: PayoutStatus = PayoutStatus.DRAFT
    transfer_id: str | None = None
    cf_transfer_id: str | None = None
    status_code: str | None = None
    reason: str | None = None
    reason_spoken: str | None = None  # owner language
    created: datetime = Field(default_factory=now)
    simulated: bool = False


class Approval(BaseModel):
    id: str
    payout_id: str
    amount: int
    option_key: str | None = None
    brief_en: str
    brief: str  # owner language
    explain_en: list[str] = Field(default_factory=list)
    token: str
    pin_required: bool = False
    state: str = "pending"  # pending | armed | approved | refused | expired | superseded
    armed_at: datetime | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None  # airpods:nod | screen:button
    ears_in: bool | None = None


class Turn(BaseModel):
    role: str  # agent | user
    text: str
    en: str | None = None
    at: datetime = Field(default_factory=now)


class Call(BaseModel):
    id: str
    purpose: str  # forwarder_release | account_change | buyer_collection | driver_photo
    party_id: str
    language: str
    channel: str = "browser"  # browser | phone
    status: str = "ringing"  # ringing | live | done | failed
    about: dict[str, Any] = Field(default_factory=dict)
    transcript: list[Turn] = Field(default_factory=list)
    findings: dict[str, Any] = Field(default_factory=dict)
    created: datetime = Field(default_factory=now)


class AuditEntry(BaseModel):
    at: datetime = Field(default_factory=now)
    kind: str
    ref: str  # id of the payout, call, obligation or approval this belongs to
    summary: str
    detail: dict[str, Any] = Field(default_factory=dict)
