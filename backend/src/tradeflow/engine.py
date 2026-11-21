"""Decision engine. Each payout gets only as much attention as its risk needs.

    Tier 0  plain code      safety rules + arithmetic (tier0.py). A broken rule = refusal.
    Tier 1  Sarvam Prism    probabilities over fixed options, ~50 ms.
    Tier 2  GLM 5.3         trade-offs between obligations, the spoken brief.
    Tier 3  the owner       anything uncertain or above the auto-pay limit.

The engine never moves money. It returns a Decision; PaymentDesk acts on it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from . import tier0
from .config import Settings
from .domain import Option, Payout, RuleResult
from .ledger import Ledger
from .money import inr
from .sarvam import Sarvam, SarvamError

log = logging.getLogger(__name__)

ROUTES = {
    "auto": "pay automatically",
    "ask_owner": "ask the owner",
    "deep": "needs deeper reasoning",
    "refuse": "refuse",
}


class Decision(BaseModel):
    route: str  # auto | ask_owner | refuse | call
    reason: str
    rules: list[RuleResult]
    prism: dict[str, Any] | None = None
    glm: dict[str, Any] | None = None
    escalated_because: list[str] = Field(default_factory=list)
    brief_en: str = ""
    explain_en: list[str] = Field(default_factory=list)
    option_key: str | None = None
    disagreement: bool = False


class Engine:
    def __init__(self, ledger: Ledger, sarvam: Sarvam, cfg: Settings) -> None:
        self.ledger = ledger
        self.sarvam = sarvam
        self.cfg = cfg

    # ------------------------------------------------------------------
    async def decide(self, payout: Payout) -> Decision:
        L = self.ledger
        rules = tier0.check_payout(L, payout)
        for r in rules:
            L.audit("rule", payout.id, f"{'PASS' if r.ok else 'FAIL'} {r.rule}: {r.detail}", r.model_dump())
        v = tier0.verdict(rules)
        failed = [r for r in rules if not r.ok]
        if v == "refuse":
            return Decision(route="refuse", reason=failed[0].detail, rules=rules)
        if v == "call":
            return Decision(route="call", reason=failed[0].detail, rules=rules)

        ctx = self._context(payout)
        opts = tier0.options(L)
        option = self._option_for(payout, opts)

        # ---- Tier 1 -----------------------------------------------------
        prism: dict[str, Any] | None = None
        escalate: list[str] = []
        try:
            answers = await self.sarvam.prism(
                ctx,
                {
                    "route": {
                        "type": "choice",
                        "criteria": ROUTES,
                        "instructions": "How should this payout be handled? Small, routine payments to known, "
                        "verified payees can be paid automatically. Large or unusual ones go to the owner.",
                    }
                },
            )
            prism = answers["route"]
            L.audit("prism", payout.id, f"Prism route: {prism['choice']} (confidence {prism['confidence']:.2f})", prism)
        except SarvamError as e:
            escalate.append(f"Prism unavailable: {e}")

        if prism:
            probs = sorted(prism["probabilities"].values(), reverse=True)
            if prism["confidence"] < 0.8:
                escalate.append(f"Prism confidence {prism['confidence']:.2f} < 0.8")
            if len(probs) > 1 and probs[0] - probs[1] < 0.15:
                escalate.append("Prism's top two options are close")
            if prism["choice"] == "deep":
                escalate.append("Prism asked for deeper reasoning")
        cash = tier0.cash_position(L)
        if cash.spendable < cash.due_7d:
            escalate.append(f"Obligations due in 7 days ({inr(cash.due_7d)}) exceed spendable cash ({inr(cash.spendable)})")
        if v == "ask_owner":
            escalate.append(failed[0].detail)

        # ---- Tier 2 -----------------------------------------------------
        glm: dict[str, Any] | None = None
        if escalate:
            glm = await self._glm(payout, ctx, opts, escalate)

        # ---- route ------------------------------------------------------
        party = L.parties[payout.party_id]
        can_auto = (
            v == "pass"
            and not escalate
            and prism is not None
            and prism["choice"] == "auto"
            and payout.amount <= self.cfg.auto_pay_limit
            and party.paid_before
        )
        disagreement = bool(glm and option and glm.get("recommend") and glm["recommend"] != option.key)
        if disagreement:
            L.audit("disagree", payout.id, f"Code ranks option {option.key} first; GLM prefers {glm['recommend']}. Owner decides.")
        if prism and prism["choice"] == "refuse" and v == "pass":
            # A model may raise a concern but cannot refuse alone; the owner hears it.
            escalate.append("Prism leaned towards refusing")

        brief_en = (glm or {}).get("brief_en") or self._template_brief(payout, option)
        explain = (glm or {}).get("reasons") or [r.detail for r in rules if r.ok][:3]
        return Decision(
            route="auto" if can_auto else "ask_owner",
            reason="Routine, verified and small" if can_auto else "; ".join(escalate) or "Above the auto-pay limit",
            rules=rules,
            prism=prism,
            glm=glm,
            escalated_because=escalate,
            brief_en=brief_en,
            explain_en=list(explain)[:3],
            option_key=option.key if option else None,
            disagreement=disagreement,
        )

    # ------------------------------------------------------------------
    def _context(self, payout: Payout) -> dict[str, Any]:
        L = self.ledger
        ob = L.obligations[payout.obligation_id]
        party = L.parties[payout.party_id]
        cash = tier0.cash_position(L)
        return {
            "payout": {"amount": payout.amount, "for": ob.label, "kind": ob.kind, "requested_by": payout.requested_by},
            "payee": {
                "role": party.role.value,
                "verified": bool(party.verification and party.verification.passes),
                "paid_before": party.paid_before,
                "account_changed": bool(party.account and not party.account.same_as(payout.to_account)),
            },
            "invoice": {"amount": ob.amount, "quoted": ob.quoted, "above_quote": ob.disputed},
            "cash": {"spendable": cash.spendable, "due_in_7_days": cash.due_7d, "expected_in_weighted": cash.expected_in_weighted},
            "auto_pay_limit": self.cfg.auto_pay_limit,
            "facts": {k: f.value for k, f in L.facts.items()},
        }

    def _option_for(self, payout: Payout, opts: list[Option]) -> Option | None:
        for o in opts:
            for line in o.pays:
                if line.obligation_id == payout.obligation_id and line.amount == payout.amount:
                    return o
        return None

    async def _glm(self, payout: Payout, ctx: dict[str, Any], opts: list[Option], why: list[str]) -> dict[str, Any] | None:
        L = self.ledger
        party = L.parties[payout.party_id]
        table = [
            {
                "key": o.key,
                "title": o.title,
                "pays_now": o.pays_now,
                "cash_left": o.cash_left,
                "feasible": o.feasible,
                "catch": o.catch,
                "code_rank": i + 1,
            }
            for i, o in enumerate(opts)
        ]
        prompt = (
            "You advise an Indian MSME exporter on which bills to pay. All arithmetic below was done by code and "
            "is correct; do not recompute or invent numbers. Use only the facts given.\n\n"
            f"Payout under review: {inr(payout.amount)} to {party.name} ({party.role.value}) for "
            f"{L.obligations[payout.obligation_id].label}.\n"
            f"Context: {json.dumps(ctx)}\n"
            f"Options ranked by code: {json.dumps(table)}\n"
            f"Escalated because: {'; '.join(why)}\n\n"
            "Return JSON only: {\"recommend\": \"A|B|C\", \"reasons\": [three short factors, most important first], "
            "\"brief_en\": \"a spoken brief for the owner, max 45 words, plain English, amounts in rupees with "
            "Indian grouping, end with 'Nod to approve.'\"}"
        )
        try:
            out = await self.sarvam.chat_json([{"role": "user", "content": prompt}], max_tokens=1500)
            L.audit("glm", payout.id, f"GLM recommends option {out.get('recommend')}", out)
            return out
        except (SarvamError, ValueError) as e:
            L.audit("glm", payout.id, f"GLM unavailable, owner decides with the code brief: {e}")
            return None

    def _template_brief(self, payout: Payout, option: Option | None) -> str:
        L = self.ledger
        party = L.parties[payout.party_id]
        s = f"Pay {inr(payout.amount)} to {party.name} for {L.obligations[payout.obligation_id].label}"
        if option:
            s += f". This is option {option.key}: {inr(option.cash_left)} left after it"
        return s + ". Nod to approve."

    # ------------------------------------------------------------------
    # Tier 1 helpers used outside payouts
    # ------------------------------------------------------------------
    async def classify(self, state: Any, question: str, options: dict[str, str]) -> dict[str, Any]:
        a = await self.sarvam.prism(state, {"q": {"type": "choice", "criteria": options, "instructions": question}})
        return a["q"]

    async def yes_no(self, state: Any, question: str) -> float:
        a = await self.sarvam.prism(state, {"q": {"type": "noul", "instructions": question}})
        return float(a["q"]["noul"])

    async def score(self, state: Any, question: str, levels: list[str]) -> dict[str, Any]:
        a = await self.sarvam.prism(state, {"q": {"type": "score", "criteria": levels, "instructions": question}})
        return a["q"]
