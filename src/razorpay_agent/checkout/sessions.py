from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.core.currency import INR, Currency
from razorpay_agent.core.decisions import GateDecision
from razorpay_agent.decision.context import DecisionContext


def to_paise(rupees: float, currency: Currency = INR) -> int:
    return currency.to_minor(rupees)


def to_rupees(paise: int, currency: Currency = INR) -> float:
    return currency.to_major(paise)


@dataclass(frozen=True)
class AppliedOffer:
    proposed_action: ProposedAction
    gate_decision: GateDecision
    arm_id: str | None
    decision_context: DecisionContext
    bandit_proposed: bool = True
    discount_paise: int = 0
    bundle_price_paise: int = 0
    audit_entry_id: int | None = None


@dataclass
class CheckoutSessionState:
    id: str
    status: str
    currency: Currency
    items: list[dict]
    allowance_max_paise: int
    allowance_expires_at: datetime
    applied_offer: AppliedOffer | None = None
    messages: list[dict] = field(default_factory=list)
    order: dict | None = None
    is_stagnant: bool = False
    days_in_stock: int | None = None


class SessionRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, CheckoutSessionState] = {}

    def new_id(self) -> str:
        return f"checkout_session_{uuid.uuid4().hex[:16]}"

    def save(self, state: CheckoutSessionState) -> None:
        with self._lock:
            self._sessions[state.id] = state

    def get(self, session_id: str) -> CheckoutSessionState | None:
        with self._lock:
            return self._sessions.get(session_id)
