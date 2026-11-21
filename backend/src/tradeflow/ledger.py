"""The Ledger holds all state for one merchant, the audit trail, and the live event feed.

Every state change goes through here so the "Why?" trail and the dashboard never drift
from what actually happened.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel

from .domain import (
    Approval,
    AuditEntry,
    Call,
    Collection,
    Obligation,
    Party,
    Payout,
    Verification,
    now,
)


class Fact(BaseModel):
    """Something the engine learned that changes a decision, e.g. forwarder_release=True."""

    value: Any
    source: str  # call:<id> | owner | rule | seed
    confidence: float | None = None


class Ledger:
    def __init__(self) -> None:
        self.merchant = {"name": "Selvam Knits", "owner": "Selvam", "city": "Tiruppur"}
        self.balance: int = 0
        self.balance_simulated: bool = True
        self.storage = {"free_hours_left": 24, "per_day": 6_000}
        self.parties: dict[str, Party] = {}
        self.obligations: dict[str, Obligation] = {}
        self.collections: dict[str, Collection] = {}
        self.payouts: dict[str, Payout] = {}
        self.approvals: dict[str, Approval] = {}
        self.calls: dict[str, Call] = {}
        self.facts: dict[str, Fact] = {}
        self.account_checks: dict[str, Verification] = {}  # account number -> latest Secure ID result
        self.audit_log: list[AuditEntry] = []
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._seq = 0

    # ---- ids --------------------------------------------------------------
    def new_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}{self._seq:04d}"

    # ---- facts ------------------------------------------------------------
    def learn(self, key: str, value: Any, source: str, confidence: float | None = None) -> None:
        self.facts[key] = Fact(value=value, source=source, confidence=confidence)
        self.audit("fact", key, f"Learned {key} = {value}", {"source": source, "confidence": confidence})

    def fact(self, key: str) -> Any:
        f = self.facts.get(key)
        return None if f is None else f.value

    # ---- audit + events ---------------------------------------------------
    def audit(self, kind: str, ref: str, summary: str, detail: dict[str, Any] | None = None) -> AuditEntry:
        entry = AuditEntry(kind=kind, ref=ref, summary=summary, detail=detail or {})
        self.audit_log.append(entry)
        self.emit("audit", entry.model_dump(mode="json"))
        return entry

    def trail(self, *refs: str) -> list[AuditEntry]:
        wanted = set(refs)
        return [e for e in self.audit_log if e.ref in wanted]

    def emit(self, event: str, data: Any) -> None:
        msg = json.dumps({"event": event, "data": data, "at": now().isoformat()}, default=str)
        for q in list(self._subscribers):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    def changed(self) -> None:
        """Tell every screen to refetch the snapshot."""
        self.emit("changed", None)
