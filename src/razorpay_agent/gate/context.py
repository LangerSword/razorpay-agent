from __future__ import annotations

import math
from dataclasses import dataclass


class InvalidContext(ValueError):
    pass


def _require_positive(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidContext(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InvalidContext(f"{field} must be a finite positive number")
    return number


@dataclass(frozen=True)
class SessionContext:
    session_id: str
    cart_value_inr: float
    buyer_allowance_inr: float
    already_offered: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise InvalidContext("session_id must be a non-empty string")
        object.__setattr__(
            self, "cart_value_inr", _require_positive(self.cart_value_inr, "cart_value_inr")
        )
        object.__setattr__(
            self,
            "buyer_allowance_inr",
            _require_positive(self.buyer_allowance_inr, "buyer_allowance_inr"),
        )
        if not isinstance(self.already_offered, bool):
            raise InvalidContext("already_offered must be a boolean")
