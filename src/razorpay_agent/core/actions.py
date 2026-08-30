from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from razorpay_agent.core.errors import ContractViolation

DISCOUNT = "discount"
BUNDLE_UPSELL = "bundle_upsell"

ACTION_TYPES = (DISCOUNT, BUNDLE_UPSELL)


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractViolation(f"{field} must be a non-empty string")
    return value


def _require_finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractViolation(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ContractViolation(f"{field} must be finite")
    return number


@dataclass(frozen=True)
class ProposedAction:
    action_type: str
    target: str
    expected_uplift: float
    confidence: float
    source: str
    session_id: str
    discount_percent: float | None = None
    bundle_item: str | None = None
    bundle_price: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.action_type, "action_type")
        if self.action_type not in ACTION_TYPES:
            raise ContractViolation(
                f"action_type must be one of {ACTION_TYPES}, got {self.action_type!r}"
            )
        _require_text(self.target, "target")
        _require_text(self.source, "source")
        _require_text(self.session_id, "session_id")
        _require_finite_number(self.expected_uplift, "expected_uplift")

        confidence = _require_finite_number(self.confidence, "confidence")
        if not 0.0 <= confidence <= 1.0:
            raise ContractViolation("confidence must be within [0, 1]")

        if self.action_type == DISCOUNT:
            self._validate_discount()
        elif self.action_type == BUNDLE_UPSELL:
            self._validate_bundle()

    def _validate_discount(self) -> None:
        if self.discount_percent is None:
            raise ContractViolation("discount actions require discount_percent")
        discount_percent = _require_finite_number(self.discount_percent, "discount_percent")
        if not 0.0 < discount_percent <= 100.0:
            raise ContractViolation("discount_percent must be within (0, 100]")
        if self.bundle_item is not None or self.bundle_price is not None:
            raise ContractViolation("discount actions must not carry bundle fields")

    def _validate_bundle(self) -> None:
        if self.bundle_item is None or self.bundle_price is None:
            raise ContractViolation("bundle_upsell actions require bundle_item and bundle_price")
        _require_text(self.bundle_item, "bundle_item")
        if _require_finite_number(self.bundle_price, "bundle_price") <= 0.0:
            raise ContractViolation("bundle_price must be positive")
        if self.discount_percent is not None:
            raise ContractViolation("bundle_upsell actions must not carry discount_percent")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "expected_uplift": self.expected_uplift,
            "confidence": self.confidence,
            "source": self.source,
            "session_id": self.session_id,
            "discount_percent": self.discount_percent,
            "bundle_item": self.bundle_item,
            "bundle_price": self.bundle_price,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProposedAction:
        try:
            return cls(
                action_type=data["action_type"],
                target=data["target"],
                expected_uplift=data["expected_uplift"],
                confidence=data["confidence"],
                source=data["source"],
                session_id=data["session_id"],
                discount_percent=data.get("discount_percent"),
                bundle_item=data.get("bundle_item"),
                bundle_price=data.get("bundle_price"),
            )
        except KeyError as exc:
            raise ContractViolation(f"missing required field {exc}") from exc
