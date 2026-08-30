from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.core.decisions import GateDecision
from razorpay_agent.core.errors import ContractViolation

ACCEPTED = "accepted"
DECLINED = "declined"
FAILED = "failed"
OFFERED = "offered"

OUTCOME_STATUSES = (ACCEPTED, DECLINED, FAILED, OFFERED)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ContractViolation(f"{field} must be a string")
    return value


@dataclass(frozen=True)
class AuditOutcome:
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in OUTCOME_STATUSES:
            raise ContractViolation(
                f"status must be one of {OUTCOME_STATUSES}, got {self.status!r}"
            )
        object.__setattr__(self, "detail", _require_text(self.detail, "detail"))

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "detail": self.detail}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditOutcome:
        try:
            return cls(status=data["status"], detail=data.get("detail", ""))
        except KeyError as exc:
            raise ContractViolation(f"missing required field {exc}") from exc


@dataclass(frozen=True)
class AuditEntry:
    timestamp: datetime
    session_id: str
    proposed_action: ProposedAction
    gate_decision: GateDecision
    outcome: AuditOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise ContractViolation("timestamp must be a datetime")
        if self.timestamp.tzinfo is None:
            raise ContractViolation("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))

        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ContractViolation("session_id must be a non-empty string")

        if not isinstance(self.proposed_action, ProposedAction):
            raise ContractViolation("proposed_action must be a ProposedAction")
        if not isinstance(self.gate_decision, GateDecision):
            raise ContractViolation("gate_decision must be a GateDecision")
        if not isinstance(self.outcome, AuditOutcome):
            raise ContractViolation("outcome must be an AuditOutcome")

        if self.proposed_action.session_id != self.session_id:
            raise ContractViolation(
                "proposed_action.session_id does not match the audit entry's session_id"
            )
        if self.gate_decision.final_action.session_id != self.session_id:
            raise ContractViolation(
                "gate_decision.final_action.session_id does not match the audit entry's session_id"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "session_id": self.session_id,
            "proposed_action": self.proposed_action.to_dict(),
            "gate_decision": self.gate_decision.to_dict(),
            "outcome": self.outcome.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEntry:
        try:
            return cls(
                timestamp=datetime.fromisoformat(data["timestamp"]),
                session_id=data["session_id"],
                proposed_action=ProposedAction.from_dict(data["proposed_action"]),
                gate_decision=GateDecision.from_dict(data["gate_decision"]),
                outcome=AuditOutcome.from_dict(data["outcome"]),
            )
        except KeyError as exc:
            raise ContractViolation(f"missing required field {exc}") from exc
