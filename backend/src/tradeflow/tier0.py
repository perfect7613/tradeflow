"""Tier 0: plain code. Cash position, payment options and safety rules.

No model ever does arithmetic, decides a safety rule, or sets a payment state.
"""

from __future__ import annotations

from datetime import timedelta

from .domain import (
    Account,
    CashPosition,
    Money,
    Obligation,
    ObligationStatus,
    Option,
    Party,
    Payout,
    PayoutStatus,
    PlanLine,
    RuleResult,
    now,
)
from .ledger import Ledger
from .money import inr, mask

# ---------------------------------------------------------------------------
# Cash position
# ---------------------------------------------------------------------------


def cash_position(ledger: Ledger) -> CashPosition:
    pending_out = sum(
        p.amount
        for p in ledger.payouts.values()
        if p.status in {PayoutStatus.SUBMITTED, PayoutStatus.SENT, PayoutStatus.UNKNOWN}
    )
    horizon = now() + timedelta(days=7)
    due_7d = sum(
        o.outstanding
        for o in ledger.obligations.values()
        if o.status in {ObligationStatus.OPEN, ObligationStatus.HELD} and o.due <= horizon
    )
    weighted = 0
    lines = [
        Money(
            label="Balance",
            amount=ledger.balance,
            kind="simulated" if ledger.balance_simulated else "confirmed",
        )
    ]
    if pending_out:
        lines.append(Money(label="Payouts not yet confirmed", amount=-pending_out, kind="pending"))
    for c in ledger.collections.values():
        left = c.amount - c.received
        if left <= 0:
            continue
        party = ledger.parties[c.party_id]
        w = collection_weight(party, c.commitment)
        weighted += int(left * w)
        lines.append(
            Money(
                label=f"{c.label} (x{w:.2f}: {_weight_reason(party, c.commitment)})",
                amount=int(left * w),
                kind="estimated",
            )
        )
    return CashPosition(
        confirmed=ledger.balance,
        pending_out=pending_out,
        expected_in_weighted=weighted,
        due_7d=due_7d,
        lines=lines,
    )


def collection_weight(buyer: Party, commitment: str | None) -> float:
    by_commitment = {
        "paid_on_call": 1.0,
        "firm_date": 0.9,
        "soft_date": 0.7,
        "vague": 0.4,
        "refused": 0.0,
    }
    if commitment in by_commitment:
        return by_commitment[commitment]
    if buyer.pays_late:
        late, total = buyer.pays_late
        return round(1 - late / total, 2)
    return 0.8


def _weight_reason(buyer: Party, commitment: str | None) -> str:
    if commitment:
        return f"said on call: {commitment.replace('_', ' ')}"
    if buyer.pays_late:
        return f"late {buyer.pays_late[0]} of last {buyer.pays_late[1]}"
    return "default"


# ---------------------------------------------------------------------------
# Options: what can we pay right now?
# ---------------------------------------------------------------------------


def options(ledger: Ledger) -> list[Option]:
    """Build A/B/C around the one disputed invoice (charged above its quote).

    A pays everything in full, B pays the disputed invoice only up to its quote,
    C holds the disputed invoice. Every other open obligation due in 7 days is paid in full.
    """
    cash = cash_position(ledger).spendable
    horizon = now() + timedelta(days=7)
    due = sorted(
        (
            o
            for o in ledger.obligations.values()
            if o.status in {ObligationStatus.OPEN, ObligationStatus.HELD} and o.due <= horizon
        ),
        key=lambda o: o.due,
    )
    disputed = next((o for o in due if o.disputed > 0), None)
    others = [o for o in due if o is not disputed]
    base = [PlanLine(obligation_id=o.id, label=o.label, amount=o.outstanding) for o in others]
    base_total = sum(line.amount for line in base)
    storage = ledger.storage

    opts: list[Option] = []
    if disputed is None:
        total = base_total
        opts.append(
            Option(
                key="A",
                title="Pay everything due",
                pays=base,
                pays_now=total,
                cash_left=cash - total,
                feasible=cash >= total,
                catch="" if cash >= total else f"Short by {inr(total - cash)}",
            )
        )
        return _rank(opts, ledger)

    release = ledger.fact("forwarder_release")  # None = unknown, True/False from the call
    full = disputed.outstanding
    at_quote = min(full, disputed.quoted or full)

    # A: everything in full
    total_a = base_total + full
    supplier = next((o for o in others if o.msme_protected), None)
    catch_a = ""
    if cash < total_a:
        catch_a = f"Short by {inr(total_a - cash)}"
        if supplier:
            catch_a += f"; {ledger.parties[supplier.party_id].name} cannot be paid in full"
    opts.append(
        Option(
            key="A",
            title="Pay everything",
            pays=base + [PlanLine(obligation_id=disputed.id, label=disputed.label, amount=full)],
            pays_now=total_a,
            cash_left=cash - total_a,
            feasible=cash >= total_a,
            catch=catch_a,
        )
    )

    # B: disputed invoice at the quoted amount only
    total_b = base_total + at_quote
    needs = None if release is True else "forwarder_release"
    if release is True:
        catch_b = f"Forwarder agreed to release on {inr(at_quote)}; {inr(disputed.disputed)} waits for proof"
    elif release is False:
        catch_b = "Forwarder refused to release on part payment"
    else:
        catch_b = f"Only works if the forwarder releases cargo on {inr(at_quote)}"
    opts.append(
        Option(
            key="B",
            title=f"Pay {disputed.label} at quote ({inr(at_quote)}), rest in full",
            pays=base + [PlanLine(obligation_id=disputed.id, label=disputed.label, amount=at_quote)],
            pays_now=total_b,
            cash_left=cash - total_b,
            feasible=cash >= total_b and release is not False,
            needs=needs,
            catch=catch_b,
        )
    )

    # C: hold the disputed invoice
    total_c = base_total
    opts.append(
        Option(
            key="C",
            title=f"Hold {disputed.label}",
            pays=base,
            pays_now=total_c,
            cash_left=cash - total_c,
            feasible=cash >= total_c,
            catch=(
                f"Cargo held; storage {inr(storage['per_day'])}/day starts in {storage['free_hours_left']} hours"
            ),
        )
    )
    return _rank(opts, ledger)


def _rank(opts: list[Option], ledger: Ledger) -> list[Option]:
    """Deterministic score. Higher is better. Infeasible options sink to the bottom."""
    per_day = ledger.storage["per_day"]
    for o in opts:
        if not o.feasible:
            o.score = -1_000_000 + o.cash_left
            continue
        score = 100.0
        if o.needs:
            score -= 40  # depends on something we have not confirmed
        if "Cargo held" in o.catch:
            score -= 30 + per_day / 1000  # storage starts, shipment slips
        score += min(o.cash_left, 50_000) / 5_000  # a little credit for a buffer
        o.score = round(score, 2)
    return sorted(opts, key=lambda o: o.score, reverse=True)


# ---------------------------------------------------------------------------
# Safety rules for a single payout
# ---------------------------------------------------------------------------


def check_payout(ledger: Ledger, payout: Payout) -> list[RuleResult]:
    ob = ledger.obligations[payout.obligation_id]
    party = ledger.parties[payout.party_id]
    results: list[RuleResult] = []

    def add(rule: str, ok: bool, detail: str, blocking: str = "refuse") -> None:
        results.append(RuleResult(rule=rule, ok=ok, detail=detail, blocking=blocking))

    # 1. Amount must be positive and not above what is still owed.
    add(
        "amount_within_outstanding",
        0 < payout.amount <= ob.outstanding,
        f"{inr(payout.amount)} against {inr(ob.outstanding)} outstanding",
    )

    # 2. Never two live payouts for one obligation; unknown outcomes are never retried blindly.
    in_flight = [
        p
        for p in ledger.payouts.values()
        if p.obligation_id == ob.id
        and p.id != payout.id
        and p.status in {PayoutStatus.SUBMITTED, PayoutStatus.SENT, PayoutStatus.UNKNOWN}
    ]
    add(
        "no_payout_in_flight",
        not in_flight,
        "No other payout pending for this obligation"
        if not in_flight
        else f"Payout {in_flight[0].id} is still {in_flight[0].status}; check its status first",
    )

    # 3. Duplicate or near-duplicate invoice.
    dup = _find_duplicate(ledger, ob)
    add(
        "not_duplicate",
        dup is None,
        "No matching invoice already paid" if dup is None else f"Looks like {dup.id} ({dup.invoice_no}), already paid",
    )

    # 4. Totals add up (line items + tax = invoice total).
    if ob.line_items:
        items = sum(a for _, a in ob.line_items)
        add(
            "totals_add_up",
            items + ob.tax == ob.amount,
            f"Items {inr(items)} + tax {inr(ob.tax)} = {inr(items + ob.tax)}; invoice says {inr(ob.amount)}",
        )

    # 5. Low-confidence extraction is asked about, never filled in silently.
    add(
        "extraction_confident",
        ob.extraction_confidence >= 0.8,
        f"Document fields read with confidence {ob.extraction_confidence:.2f}",
        blocking="ask_owner",
    )

    # 6. The payee's account must be verified by Secure ID.
    v = party.verification if party.account and party.account.same_as(payout.to_account) else None
    if party.account and party.account.same_as(payout.to_account):
        add(
            "payee_verified",
            bool(v and v.passes),
            f"Secure ID: {v.account_status} / {v.name_match}" if v else "Not yet verified",
        )

    # 7. A changed account needs a confirmation call to the number saved at onboarding,
    #    and the new account must also verify. A verified account is not proof the change
    #    was authorised.
    changed = party.account is not None and not party.account.same_as(payout.to_account)
    if changed:
        key = f"account_change_confirmed:{party.id}:{payout.to_account.last4}"
        confirmed = ledger.fact(key)
        if confirmed is None:
            add(
                "account_change_confirmed",
                False,
                f"Requested account {mask(payout.to_account.number)} differs from saved "
                f"{mask(party.account.number)}; call {party.name} on the saved number",
                blocking="call",
            )
        else:
            add(
                "account_change_confirmed",
                bool(confirmed),
                f"{party.name} {'confirmed' if confirmed else 'denied'} the change on a call to the saved number",
            )
        check = ledger.account_checks.get(payout.to_account.number)
        add(
            "new_account_verified",
            bool(check and check.passes),
            f"Secure ID on {mask(payout.to_account.number)}: {check.account_status} / {check.name_match}"
            + (f", name at bank {check.name_at_bank}" if check and check.name_at_bank else "")
            if check
            else f"{mask(payout.to_account.number)} not verified yet",
        )

    # 8. Cash: never pay what we do not have.
    spendable = cash_position(ledger).spendable
    add("cash_available", spendable >= payout.amount, f"Spendable {inr(spendable)} for {inr(payout.amount)}")

    return results


def _find_duplicate(ledger: Ledger, ob: Obligation) -> Obligation | None:
    for other in ledger.obligations.values():
        if other.id == ob.id or other.paid == 0:
            continue
        same_party = other.party_id == ob.party_id
        if same_party and ob.invoice_no and other.invoice_no == ob.invoice_no:
            return other
        if same_party and other.amount == ob.amount and abs((other.due - ob.due).days) <= 3 and other.kind == ob.kind:
            return other
    return None


def verdict(results: list[RuleResult]) -> str:
    """refuse beats call beats ask_owner beats pass."""
    failed = [r for r in results if not r.ok]
    for level in ("refuse", "call", "ask_owner"):
        if any(r.blocking == level for r in failed):
            return level
    return "pass"


def account_for(party: Party, requested: Account | None) -> Account:
    if requested is not None:
        return requested
    if party.account is None:
        raise ValueError(f"{party.name} has no saved account")
    return party.account
