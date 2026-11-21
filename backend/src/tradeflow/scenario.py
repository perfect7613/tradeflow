"""Synthetic hero scenario and the trap scorecard.

All numbers are synthetic and chosen so that no option is obviously right.
Account numbers are Cashfree's published sandbox test accounts.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

from pydantic import BaseModel

from . import tier0
from .domain import (
    Account,
    Collection,
    Obligation,
    Party,
    Payout,
    PayoutStatus,
    Role,
    Verification,
    now,
)
from .ledger import Ledger

TRUCK_ACC = Account(number="026291800001191", ifsc="YESB0000262", holder="Ravi Kumar")
BROTHER_ACC = Account(number="388108022658", ifsc="ICIC0000009", holder="Senthil Kumar")  # the driver says: pay my brother Senthil
FWD_ACC = Account(number="00011020001772", ifsc="HDFC0000001", holder="Coastline Logistics")
SUPPLIER_ACC = Account(number="1233943142", ifsc="ICIC0000009", holder="Sri Murugan Yarns")

BANK_NAMES = {
    TRUCK_ACC.number: "RAVI KUMAR",
    BROTHER_ACC.number: "SENTHIL KUMAR",
    FWD_ACC.number: "COASTLINE LOGISTICS",
    SUPPLIER_ACC.number: "SRI MURUGAN YARNS",
}

SAMPLE_INVOICE = {
    "vendor_name": "Coastline Logistics",
    "invoice_no": "CL/2026/1187",
    "total": 50_000,
    "quoted": 32_000,
    "tax": 0,
    "line_items": [("Ocean freight, Chennai to Rotterdam", 32_000), ("Detention charges", 18_000)],
    "confidence": 0.93,
}


def _ok(holder: str) -> Verification:
    return Verification(account_status="VALID", name_match="DIRECT_MATCH", name_at_bank=holder.upper(), simulated=True)


def seed(L: Ledger) -> None:
    t = now()
    L.balance = 2_30_000
    L.balance_simulated = True
    L.parties = {
        "truck": Party(id="truck", name="Ravi Kumar", role=Role.TRUCK_OWNER, phone="+919800000001", language="ta-IN",
                       account=TRUCK_ACC, verification=_ok("Ravi Kumar"), paid_before=True),
        "driver": Party(id="driver", name="Murugan", role=Role.DRIVER, phone="+919800000002", language="ta-IN"),
        "fwd": Party(id="fwd", name="Coastline Logistics", role=Role.FORWARDER, phone="+919800000003", language="ta-IN",
                     account=FWD_ACC, verification=_ok("Coastline Logistics"), paid_before=True),
        "supplier": Party(id="supplier", name="Sri Murugan Yarns", role=Role.SUPPLIER, phone="+919800000004", language="ta-IN",
                          account=SUPPLIER_ACC, verification=_ok("Sri Murugan Yarns"), udyam="UDYAM-TN-31-0012345", paid_before=True),
        "buyer": Party(id="buyer", name="Brightwear Sourcing", role=Role.BUYER, phone="+919800000005", language="en-IN",
                       pays_late=(2, 5)),
    }
    L.obligations = {
        "truck_bal": Obligation(id="truck_bal", party_id="truck", kind="truck_balance", label="Truck balance, Tiruppur to Chennai port",
                                amount=12_000, due=t + timedelta(hours=6)),
        "yarn": Obligation(id="yarn", party_id="supplier", kind="supplier", label="Yarn bill", amount=1_80_000,
                           due=t + timedelta(days=1), invoice_no="SMY-4471", msme_protected=True),
    }
    L.collections = {
        "buyer_po": Collection(id="buyer_po", party_id="buyer", label="Buyer payment, PO 2291", amount=2_50_000,
                               expected=t + timedelta(days=3)),
    }
    L.storage = {"free_hours_left": 24, "per_day": 6_000}
    L.audit("seed", "scenario", "Loaded the Tiruppur hero scenario (synthetic numbers)")


def add_forwarder_invoice(L: Ledger, fields: dict, source: str) -> Obligation:
    ob = Obligation(
        id="fwd_inv",
        party_id="fwd",
        kind="freight",
        label="Forwarder invoice",
        amount=int(fields["total"]),
        quoted=int(fields.get("quoted") or SAMPLE_INVOICE["quoted"]),
        due=now() + timedelta(hours=24),
        invoice_no=fields.get("invoice_no"),
        line_items=[(str(d), int(a)) for d, a in fields.get("line_items", [])],
        tax=int(fields.get("tax") or 0),
        extraction_confidence=float(fields.get("confidence", 1.0)),
        source=source,
    )
    L.obligations[ob.id] = ob
    return ob


# ---------------------------------------------------------------------------
# Trap scorecard (Tier 0 only: deterministic, no model calls)
# ---------------------------------------------------------------------------


class TrapCase(BaseModel):
    name: str
    trap: bool  # True = should be stopped, False = a good payment that should pass
    verdict: str = ""
    caught: bool = False
    detail: str = ""


def _payout(L: Ledger, ob_id: str, amount: int | None = None, to: Account | None = None) -> Payout:
    ob = L.obligations[ob_id]
    party = L.parties[ob.party_id]
    return Payout(id="trap", obligation_id=ob_id, party_id=party.id, amount=amount or ob.outstanding,
                  to_account=to or party.account, requested_by="scorecard")  # type: ignore[arg-type]


def trap_cases() -> list[tuple[str, bool, Callable[[Ledger], Payout]]]:
    def fwd(L: Ledger) -> None:
        add_forwarder_invoice(L, SAMPLE_INVOICE, "scorecard")

    def duplicate(L: Ledger) -> Payout:
        L.obligations["yarn"].paid = L.obligations["yarn"].amount
        dup = L.obligations["yarn"].model_copy(update={"id": "yarn2", "paid": 0})
        L.obligations["yarn2"] = dup
        return _payout(L, "yarn2")

    def near_duplicate(L: Ledger) -> Payout:
        L.obligations["truck_bal"].paid = 12_000
        L.obligations["truck2"] = L.obligations["truck_bal"].model_copy(update={"id": "truck2", "paid": 0, "invoice_no": "LR-99"})
        return _payout(L, "truck2")

    def changed_unconfirmed(L: Ledger) -> Payout:
        # The new account is real and verifies; that alone is not proof the change was authorised.
        L.account_checks[BROTHER_ACC.number] = Verification(account_status="VALID", name_match="DIRECT_MATCH", name_at_bank="SENTHIL KUMAR")
        return _payout(L, "truck_bal", to=BROTHER_ACC)

    def changed_denied(L: Ledger) -> Payout:
        L.learn(f"account_change_confirmed:truck:{BROTHER_ACC.last4}", False, "scorecard")
        return changed_unconfirmed(L)

    def changed_confirmed_but_unverified(L: Ledger) -> Payout:
        L.learn(f"account_change_confirmed:truck:{BROTHER_ACC.last4}", True, "scorecard")
        L.account_checks[BROTHER_ACC.number] = Verification(account_status="VALID", name_match="NO_MATCH", name_at_bank="ARUN TRADERS")
        return _payout(L, "truck_bal", to=BROTHER_ACC)

    def wrong_totals(L: Ledger) -> Payout:
        fwd(L)
        L.obligations["fwd_inv"].line_items = [("Ocean freight", 32_000), ("Detention", 16_000)]
        return _payout(L, "fwd_inv", 32_000)

    def smudged(L: Ledger) -> Payout:
        fwd(L)
        L.obligations["fwd_inv"].extraction_confidence = 0.52
        return _payout(L, "fwd_inv", 32_000)

    def name_mismatch(L: Ledger) -> Payout:
        L.parties["supplier"].verification = Verification(account_status="VALID", name_match="POOR_PARTIAL_MATCH", name_at_bank="MURUGAN TEXTILES")
        return _payout(L, "yarn")

    def invalid_account(L: Ledger) -> Payout:
        L.parties["supplier"].verification = Verification(account_status="INVALID", name_match="NO_MATCH")
        return _payout(L, "yarn")

    def unverified(L: Ledger) -> Payout:
        L.parties["supplier"].verification = None
        return _payout(L, "yarn")

    def over_outstanding(L: Ledger) -> Payout:
        return _payout(L, "truck_bal", 15_000)

    def in_flight(L: Ledger) -> Payout:
        L.payouts["p_old"] = Payout(id="p_old", obligation_id="yarn", party_id="supplier", amount=1_80_000,
                                    to_account=SUPPLIER_ACC, requested_by="earlier", status=PayoutStatus.UNKNOWN)
        return _payout(L, "yarn")

    def no_cash(L: Ledger) -> Payout:
        L.balance = 1_00_000
        return _payout(L, "yarn")

    def good_truck(L: Ledger) -> Payout:
        return _payout(L, "truck_bal")

    def good_quote(L: Ledger) -> Payout:
        fwd(L)
        return _payout(L, "fwd_inv", 32_000)

    def good_change(L: Ledger) -> Payout:
        L.learn(f"account_change_confirmed:truck:{BROTHER_ACC.last4}", True, "scorecard")
        L.account_checks[BROTHER_ACC.number] = Verification(account_status="VALID", name_match="DIRECT_MATCH", name_at_bank="SENTHIL KUMAR")
        return _payout(L, "truck_bal", to=BROTHER_ACC)

    return [
        ("Duplicate invoice (same number, already paid)", True, duplicate),
        ("Near-duplicate truck balance", True, near_duplicate),
        ("Changed account, not yet confirmed by call", True, changed_unconfirmed),
        ("Changed account, owner denied on call", True, changed_denied),
        ("Changed account confirmed, but new account fails name match", True, changed_confirmed_but_unverified),
        ("Invoice totals do not add up", True, wrong_totals),
        ("Smudged amount (low extraction confidence)", True, smudged),
        ("Payee name mismatch at bank", True, name_mismatch),
        ("Invalid bank account", True, invalid_account),
        ("Payee never verified", True, unverified),
        ("Amount above what is owed", True, over_outstanding),
        ("Earlier payout still unknown (no blind retry)", True, in_flight),
        ("Not enough cash", True, no_cash),
        ("Good: routine truck balance", False, good_truck),
        ("Good: forwarder at quote", False, good_quote),
        ("Good: account change confirmed and verified", False, good_change),
    ]


def run_scorecard() -> dict:
    results: list[TrapCase] = []
    for name, trap, build in trap_cases():
        L = Ledger()
        seed(L)
        payout = build(L)
        rules = tier0.check_payout(L, payout)
        v = tier0.verdict(rules)
        failed = [r for r in rules if not r.ok]
        stopped = v != "pass"
        results.append(
            TrapCase(
                name=name,
                trap=trap,
                verdict=v,
                caught=stopped if trap else not stopped,
                detail=failed[0].detail if failed else "All rules pass",
            )
        )
    traps = [r for r in results if r.trap]
    good = [r for r in results if not r.trap]
    return {
        "cases": [r.model_dump() for r in results],
        "traps_caught": sum(r.caught for r in traps),
        "traps_total": len(traps),
        "good_wrongly_blocked": sum(not r.caught for r in good),
        "good_total": len(good),
        "note": "Tier 0 rules only, run on synthetic cases. Model tiers are not involved in this score.",
    }
