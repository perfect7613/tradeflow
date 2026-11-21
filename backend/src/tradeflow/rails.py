"""Payment rails: the seam between TradeFlow and Cashfree.

Two adapters satisfy the same interface:
  * CashfreeRails - real sandbox/production APIs (Secure ID, Payouts V2, Payment Links).
  * SimRails      - in-process stand-in used until keys are configured. It replays the
                    same webhook shapes Cashfree sends, so the rest of the app cannot tell
                    the difference except for the `simulated` flag shown in the UI.

Payout calls are only ever made by PaymentDesk after rules pass and the owner approves.
No model is given access to this module.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

import httpx

from .config import Settings
from .domain import Account, Party, Verification

log = logging.getLogger(__name__)

WebhookSink = Callable[[str, dict[str, Any]], Awaitable[None]]  # (product, payload)


@dataclass
class TransferState:
    transfer_id: str
    status: str  # RECEIVED | PENDING | SUCCESS | FAILED | REJECTED | REVERSED | ...
    status_code: str | None = None
    cf_transfer_id: str | None = None
    description: str | None = None


@dataclass
class Link:
    link_id: str
    link_url: str
    status: str


class Rails(Protocol):
    simulated: bool

    async def verify_account(self, account: Account, phone: str) -> Verification: ...
    async def ensure_beneficiary(self, party: Party, account: Account) -> str: ...
    async def pay(self, transfer_id: str, beneficiary_id: str, amount: int, remarks: str) -> TransferState: ...
    async def transfer_status(self, transfer_id: str) -> TransferState: ...
    async def create_link(
        self, link_id: str, amount: int, name: str, phone: str, purpose: str, min_partial: int | None
    ) -> Link: ...
    def verify_webhook(self, product: str, raw: bytes, timestamp: str, signature: str) -> bool: ...


def webhook_signature(secret: str, timestamp: str, raw: bytes) -> str:
    """Cashfree: Base64(HMAC-SHA256(timestamp + rawBody, clientSecret))."""
    msg = timestamp.encode() + raw
    return base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()


def _bene_id(party: Party, account: Account) -> str:
    # Alphanumeric + underscore only. Tied to the account, so a new account is a new beneficiary.
    return f"tf_{party.id}_{account.last4}"


# ---------------------------------------------------------------------------
# Real Cashfree
# ---------------------------------------------------------------------------


class CashfreeRails:
    simulated = False

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        host = "https://sandbox.cashfree.com" if cfg.cashfree_env == "sandbox" else "https://api.cashfree.com"
        self.payouts = httpx.AsyncClient(base_url=f"{host}/payout", timeout=30)
        self.verify = httpx.AsyncClient(base_url=f"{host}/verification", timeout=30)
        self.pg = httpx.AsyncClient(base_url=f"{host}/pg", timeout=30)

    # -- auth -------------------------------------------------------------
    def _headers(self, product: str) -> dict[str, str]:
        if product == "payouts":
            cid, secret, key = (
                self.cfg.cashfree_payouts_client_id,
                self.cfg.cashfree_payouts_client_secret,
                self.cfg.cashfree_payouts_public_key_path,
            )
            version = "2024-01-01"
        elif product == "verify":
            cid, secret, key = (
                self.cfg.cashfree_verify_client_id or self.cfg.cashfree_payouts_client_id,
                self.cfg.cashfree_verify_client_secret or self.cfg.cashfree_payouts_client_secret,
                self.cfg.cashfree_verify_public_key_path,
            )
            version = "2024-01-01"
        else:
            cid, secret, key = self.cfg.cashfree_pg_client_id, self.cfg.cashfree_pg_client_secret, ""
            version = "2025-01-01"
        h = {
            "x-client-id": cid,
            "x-client-secret": secret,
            "x-api-version": version,
            "Content-Type": "application/json",
        }
        if key:
            h["x-cf-signature"] = _cf_signature(cid, key)
        return h

    async def _call(self, client: httpx.AsyncClient, product: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
        r = await client.request(method, path, headers=self._headers(product), **kw)
        if r.status_code >= 400:
            raise RailsError(product, r.status_code, r.text)
        return r.json()

    # -- Secure ID --------------------------------------------------------
    async def verify_account(self, account: Account, phone: str) -> Verification:
        data = await self._call(
            self.verify,
            "verify",
            "POST",
            "/bank-account/sync",
            json={"bank_account": account.number, "ifsc": account.ifsc, "name": account.holder, "phone": phone},
        )
        return Verification(
            account_status=data.get("account_status", "INVALID"),
            name_match=data.get("name_match_result", "NO_MATCH"),
            name_at_bank=data.get("name_at_bank", ""),
            reference_id=str(data.get("reference_id", "")),
        )

    # -- Payouts V2 -------------------------------------------------------
    async def ensure_beneficiary(self, party: Party, account: Account) -> str:
        bene_id = _bene_id(party, account)
        try:
            await self._call(self.payouts, "payouts", "GET", "/beneficiary", params={"beneficiary_id": bene_id})
            return bene_id
        except RailsError as e:
            if e.status != 404:
                raise
        await self._call(
            self.payouts,
            "payouts",
            "POST",
            "/beneficiary",
            json={
                "beneficiary_id": bene_id,
                "beneficiary_name": _alpha(account.holder),
                "beneficiary_instrument_details": {"bank_account_number": account.number, "bank_ifsc": account.ifsc},
                "beneficiary_contact_details": {"beneficiary_phone": phone_digits(party.phone)},
            },
        )
        return bene_id

    async def pay(self, transfer_id: str, beneficiary_id: str, amount: int, remarks: str) -> TransferState:
        body: dict[str, Any] = {
            "transfer_id": transfer_id,
            "transfer_amount": amount,
            "transfer_mode": "banktransfer",
            "transfer_remarks": _alnum(remarks)[:70],
            "beneficiary_details": {"beneficiary_id": beneficiary_id},
        }
        if self.cfg.cashfree_fundsource_id:
            body["fundsource_id"] = self.cfg.cashfree_fundsource_id
        data = await self._call(self.payouts, "payouts", "POST", "/transfers", json=body)
        return _transfer(data)

    async def transfer_status(self, transfer_id: str) -> TransferState:
        data = await self._call(self.payouts, "payouts", "GET", "/transfers", params={"transfer_id": transfer_id})
        return _transfer(data)

    # -- Payment Links ----------------------------------------------------
    async def create_link(
        self, link_id: str, amount: int, name: str, phone: str, purpose: str, min_partial: int | None
    ) -> Link:
        body: dict[str, Any] = {
            "link_id": link_id,
            "link_amount": amount,
            "link_currency": "INR",
            "link_purpose": purpose[:500],
            "customer_details": {"customer_name": name, "customer_phone": phone_digits(phone)},
            "link_notify": {"send_sms": False, "send_email": False},
            "link_auto_reminders": True,
            "link_meta": {"notify_url": f"{self.cfg.public_url}/webhooks/cashfree/links"},
        }
        if min_partial:
            body["link_partial_payments"] = True
            body["link_minimum_partial_amount"] = min_partial
        data = await self._call(self.pg, "pg", "POST", "/links", json=body)
        return Link(link_id=data["link_id"], link_url=data["link_url"], status=data.get("link_status", "ACTIVE"))

    def verify_webhook(self, product: str, raw: bytes, timestamp: str, signature: str) -> bool:
        secret = (
            self.cfg.cashfree_pg_client_secret if product == "links" else self.cfg.cashfree_payouts_client_secret
        )
        return bool(secret) and hmac.compare_digest(webhook_signature(secret, timestamp, raw), signature or "")


class RailsError(RuntimeError):
    def __init__(self, product: str, status: int, body: str) -> None:
        super().__init__(f"Cashfree {product} {status}: {body[:300]}")
        self.status = status
        self.body = body


def _transfer(data: dict[str, Any]) -> TransferState:
    return TransferState(
        transfer_id=data.get("transfer_id", ""),
        status=data.get("status", "UNKNOWN"),
        status_code=data.get("status_code"),
        cf_transfer_id=str(data.get("cf_transfer_id") or "") or None,
        description=data.get("status_description"),
    )


def _cf_signature(client_id: str, pem_path: str) -> str:
    """2FA public key signature: RSA-OAEP(clientId.unixTimestamp), base64. Valid for 5 minutes."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    with open(pem_path, "rb") as f:
        key = serialization.load_pem_public_key(f.read())
    msg = f"{client_id}.{int(time.time())}".encode()
    enc = key.encrypt(msg, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()), algorithm=hashes.SHA1(), label=None))  # type: ignore[union-attr]
    return base64.b64encode(enc).decode()


def _alpha(s: str) -> str:
    return "".join(ch for ch in s if ch.isalpha() or ch == " ").strip() or "Beneficiary"


def _alnum(s: str) -> str:
    return "".join(ch for ch in s if ch.isalnum() or ch == " ")


def phone_digits(phone: str) -> str:
    d = "".join(ch for ch in phone if ch.isdigit())
    return d[-10:]


# ---------------------------------------------------------------------------
# Simulated rails
# ---------------------------------------------------------------------------

# Outcomes mirror Cashfree's published sandbox test accounts.
SIM_OUTCOMES = {
    "026291800001190": "FAILED",
    "02014457596969": "REVERSED",
    "007711000031": "PENDING",
}


class SimRails:
    simulated = True

    def __init__(self, cfg: Settings) -> None:
        self.cfg = cfg
        self.sink: WebhookSink | None = None
        self.bank_names: dict[str, str] = {}  # account number -> name at bank
        self.transfers: dict[str, TransferState] = {}
        self.accounts: dict[str, str] = {}  # beneficiary_id -> account number
        self.links: dict[str, dict[str, Any]] = {}
        self.latency_s = 1.6

    async def verify_account(self, account: Account, phone: str) -> Verification:
        await asyncio.sleep(0.3)
        name = self.bank_names.get(account.number)
        if name is None:
            return Verification(account_status="INVALID", name_match="NO_MATCH", simulated=True)
        return Verification(
            account_status="VALID",
            name_match=_name_match(account.holder, name),
            name_at_bank=name,
            reference_id=f"sim{abs(hash(account.number)) % 100000}",
            simulated=True,
        )

    async def ensure_beneficiary(self, party: Party, account: Account) -> str:
        bene = _bene_id(party, account)
        self.accounts[bene] = account.number
        return bene

    async def pay(self, transfer_id: str, beneficiary_id: str, amount: int, remarks: str) -> TransferState:
        if transfer_id in self.transfers:
            # Cashfree rejects a reused transfer_id; so do we.
            raise RailsError("payouts", 409, '{"code":"transfer_id_already_exists"}')
        st = TransferState(transfer_id=transfer_id, status="RECEIVED", status_code="RECEIVED", cf_transfer_id=f"sim{len(self.transfers) + 1:05d}")
        self.transfers[transfer_id] = st
        outcome = SIM_OUTCOMES.get(self.accounts.get(beneficiary_id, ""), "SUCCESS")
        asyncio.create_task(self._settle(st, beneficiary_id, amount, outcome))
        return st

    async def _settle(self, st: TransferState, bene: str, amount: int, outcome: str) -> None:
        await asyncio.sleep(self.latency_s)
        if outcome == "PENDING":
            return
        steps = {
            "SUCCESS": [("TRANSFER_SUCCESS", "SUCCESS", "COMPLETED")],
            "FAILED": [("TRANSFER_FAILED", "FAILED", "INVALID_ACCOUNT_FAIL")],
            "REVERSED": [("TRANSFER_SUCCESS", "SUCCESS", "COMPLETED"), ("TRANSFER_REVERSED", "REVERSED", "INVALID_ACCOUNT_FAIL")],
        }[outcome]
        for i, (event, status, code) in enumerate(steps):
            if i:
                await asyncio.sleep(self.latency_s * 2)
            st.status, st.status_code = status, code
            if self.sink:
                await self.sink(
                    "payouts",
                    {
                        "type": event,
                        "event_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "data": {
                            "transfer_id": st.transfer_id,
                            "cf_transfer_id": st.cf_transfer_id,
                            "status": status,
                            "status_code": code,
                            "transfer_amount": amount,
                            "beneficiary_details": {"beneficiary_id": bene},
                            "simulated": True,
                        },
                    },
                )

    async def transfer_status(self, transfer_id: str) -> TransferState:
        st = self.transfers.get(transfer_id)
        if st is None:
            raise RailsError("payouts", 404, '{"code":"transfer_not_found"}')
        return st

    async def create_link(
        self, link_id: str, amount: int, name: str, phone: str, purpose: str, min_partial: int | None
    ) -> Link:
        url = f"{self.cfg.public_url}/sim-pay?link={link_id}"
        self.links[link_id] = {"amount": amount, "paid": 0, "name": name, "purpose": purpose, "min": min_partial or amount}
        return Link(link_id=link_id, link_url=url, status="ACTIVE")

    async def sim_pay_link(self, link_id: str, amount: int) -> None:
        link = self.links[link_id]
        link["paid"] += amount
        status = "PAID" if link["paid"] >= link["amount"] else "PARTIALLY_PAID"
        if self.sink:
            await self.sink(
                "links",
                {
                    "type": "PAYMENT_LINK_EVENT",
                    "data": {
                        "link_id": link_id,
                        "link_status": status,
                        "link_amount": str(link["amount"]),
                        "link_amount_paid": str(link["paid"]),
                        "order": {"order_amount": str(amount), "transaction_status": "SUCCESS"},
                        "simulated": True,
                    },
                },
            )

    def verify_webhook(self, product: str, raw: bytes, timestamp: str, signature: str) -> bool:
        return False  # simulated webhooks are delivered in-process, never over HTTP


def _name_match(expected: str, at_bank: str) -> str:
    a = {w for w in expected.upper().replace(".", " ").split() if len(w) > 1}
    b = {w for w in at_bank.upper().replace(".", " ").split() if len(w) > 1}
    if a == b:
        return "DIRECT_MATCH"
    overlap = len(a & b) / max(1, len(a | b))
    if overlap >= 0.5:
        return "GOOD_PARTIAL_MATCH"
    if overlap > 0:
        return "POOR_PARTIAL_MATCH"
    return "NO_MATCH"


def make_rails(cfg: Settings) -> CashfreeRails | SimRails:
    return CashfreeRails(cfg) if cfg.cashfree_live else SimRails(cfg)
