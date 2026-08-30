from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.core.errors import ContractViolation


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractViolation(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    checked_against: tuple[str, ...]
    reason: str
    final_action: ProposedAction

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ContractViolation("allowed must be a boolean")

        checked_against = tuple(self._validated_checks(self.checked_against))
        if not checked_against:
            raise ContractViolation("checked_against must name at least one limit")
        object.__setattr__(self, "checked_against", checked_against)

        _require_text(self.reason, "reason")

        if not isinstance(self.final_action, ProposedAction):
            raise ContractViolation("final_action must be a ProposedAction")

    @staticmethod
    def _validated_checks(checks: Iterable[str]) -> list[str]:
        if isinstance(checks, str) or not isinstance(checks, (list, tuple)):
            raise ContractViolation("checked_against must be a list of limit names")
        validated = []
        for check in checks:
            _require_text(check, "checked_against entry")
            if check not in validated:
                validated.append(check)
        return validated

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "checked_against": list(self.checked_against),
            "reason": self.reason,
            "final_action": self.final_action.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GateDecision:
        try:
            return cls(
                allowed=data["allowed"],
                checked_against=data["checked_against"],
                reason=data["reason"],
                final_action=ProposedAction.from_dict(data["final_action"]),
            )
        except KeyError as exc:
            raise ContractViolation(f"missing required field {exc}") from exc
