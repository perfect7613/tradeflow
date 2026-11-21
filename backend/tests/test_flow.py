"""End-to-end through TradeFlow with a fake Sarvam and the simulated Cashfree rails."""

import asyncio
from datetime import timedelta

import pytest

from tradeflow.domain import PayoutStatus, Turn
from tradeflow.payments import ApprovalError

pytestmark = pytest.mark.asyncio


async def settle(tf, payout_id, want, tries=100):
    for _ in range(tries):
        if tf.L.payouts[payout_id].status == want:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"{payout_id} stuck at {tf.L.payouts[payout_id].status}")


async def forwarder_agrees(tf):
    await tf.invoice()
    call = await tf.call_forwarder()
    await tf.finish_call(call.id, [{"role": "user", "text": "It is detention. Pay 32000 today and we release."}])
    approval = next(iter(tf.L.approvals.values()))
    return approval, tf.L.payouts[approval.payout_id]


async def test_hero_forwarder_call_then_nod_pays_32000(tf):
    approval, payout = await forwarder_agrees(tf)
    assert tf.L.fact("forwarder_release") is True
    assert payout.amount == 32_000 and payout.status == PayoutStatus.AWAITING_OWNER
    assert approval.option_key == "B"

    tf.payments.arm(approval.id)
    await tf.payments.respond(approval.id, approval.token, "nod", "airpods", ears_in=True)
    await settle(tf, payout.id, PayoutStatus.PAID)
    assert tf.L.balance == 1_98_000
    assert tf.L.obligations["fwd_inv"].outstanding == 18_000  # waits for detention proof


async def test_nod_rules(tf):
    approval, _ = await forwarder_agrees(tf)
    with pytest.raises(ApprovalError, match="not finished"):
        await tf.payments.respond(approval.id, approval.token, "nod", "airpods", ears_in=True)
    tf.payments.arm(approval.id)
    with pytest.raises(ApprovalError, match="not in ears"):
        await tf.payments.respond(approval.id, approval.token, "nod", "airpods", ears_in=False)
    with pytest.raises(ApprovalError, match="token"):
        await tf.payments.respond(approval.id, "replayed", "nod", "airpods", ears_in=True)
    approval.armed_at -= timedelta(seconds=11)
    with pytest.raises(ApprovalError, match="expired"):
        await tf.payments.respond(approval.id, approval.token, "nod", "airpods", ears_in=True)


async def test_token_is_one_time(tf):
    approval, _ = await forwarder_agrees(tf)
    token = approval.token
    await tf.payments.respond(approval.id, token, "approve", "screen")
    with pytest.raises(ApprovalError):
        await tf.payments.respond(approval.id, token, "approve", "screen")


async def test_account_change_denied_by_real_owner_is_refused(tf):
    tf.sarvam.noul = 0.04  # Prism: he did not ask for the change
    result = await tf.driver_note(text="Anna, send the balance to my brother Senthil's account")
    payout = tf.L.payouts[result["payout"]]
    assert payout.status == PayoutStatus.NEEDS_CALL
    call = next(c for c in tf.L.calls.values() if c.purpose == "account_change")
    await tf.calls.finish(call, [Turn(role="user", text="No, I never asked for that.")])
    assert payout.status == PayoutStatus.REFUSED
    assert "denied" in payout.reason
    assert not tf.rails.transfers  # no money moved


async def test_account_change_confirmed_and_verified_goes_to_owner(tf):
    tf.sarvam.noul = 0.97
    result = await tf.driver_note(text="Pay my brother Senthil instead")
    call = next(c for c in tf.L.calls.values() if c.purpose == "account_change")
    await tf.calls.finish(call, [Turn(role="user", text="Yes, I asked, pay Senthil.")])
    assert tf.L.payouts[result["payout"]].status in {PayoutStatus.AWAITING_OWNER, PayoutStatus.SUBMITTED}


async def test_reversal_reopens_without_paying_twice(tf):
    approval, payout = await forwarder_agrees(tf)
    await tf.payments.respond(approval.id, approval.token, "approve", "screen")
    await settle(tf, payout.id, PayoutStatus.PAID)
    first_id = payout.transfer_id
    await tf.webhook("payouts", {"type": "TRANSFER_REVERSED", "data": {"transfer_id": first_id, "status": "REVERSED", "status_code": "INVALID_ACCOUNT_FAIL"}})
    ob = tf.L.obligations["fwd_inv"]
    assert payout.status == PayoutStatus.REVERSED
    assert ob.outstanding == 50_000 and ob.attempt == 2
    assert tf.L.balance == 2_30_000
    # the same webhook twice changes nothing
    await tf.webhook("payouts", {"type": "TRANSFER_REVERSED", "data": {"transfer_id": first_id, "status": "REVERSED"}})
    assert tf.L.balance == 2_30_000


async def test_buyer_link_paid_during_call_updates_cash(tf):
    call = await tf.call_buyer()
    url = await tf._make_link(call, 1)
    link_id = tf.L.collections["buyer_po"].link_id
    await tf.rails.sim_pay_link(link_id, 1)
    assert tf.L.collections["buyer_po"].received == 1
    assert tf.L.balance == 2_30_001
    assert "sim-pay" in url


async def test_amend_lowers_amount_with_new_approval(tf):
    approval, payout = await forwarder_agrees(tf)
    new = await tf.payments.amend(approval.id, 30_000)
    assert new.amount == 30_000 and new.id != approval.id
    assert tf.L.approvals[approval.id].state == "superseded"
