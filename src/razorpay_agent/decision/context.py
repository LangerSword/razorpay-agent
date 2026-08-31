from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

RUPEES_PER_UNIT = 1000.0
# static features: intercept, (1 - is_stagnant) * cart/1000, allowance ratio,
# is_stagnant, cart/1000 * days_in_stock/100 (stagnant clearance relief), and
# is_stagnant * cart/1000 (stagnant non-clearance penalty).
#
# The cart term is gated by (1 - is_stagnant) so the normal net-revenue margin and the
# stagnant relief/penalty live on DISJOINT features — normal uses feature 1 only, stale
# uses features 4 and 5 only. This is what stops the large stagnant relief/cart signal
# from leaking into the shared cart coefficient and contaminating normal-item scores
# (a plain cart feature is collinear with is_stagnant*cart on stagnant sessions, so it
# cannot separate them). The standalone days_in_stock feature is deliberately absent:
# it would be exactly collinear with (cart * days) and misattribute the relief signal.
_STATIC_FEATURES = 6


class InvalidDecisionInput(ValueError):
    pass


def _require_positive(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidDecisionInput(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise InvalidDecisionInput(f"{field} must be a finite positive number")
    return number


@dataclass(frozen=True)
class DecisionContext:
    session_id: str
    target_sku: str
    item_category: str
    cart_value_inr: float
    buyer_allowance_inr: float
    is_stagnant: bool = False
    days_in_stock: int | None = None

    def __post_init__(self) -> None:
        for field in ("session_id", "target_sku", "item_category"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise InvalidDecisionInput(f"{field} must be a non-empty string")
        object.__setattr__(
            self, "cart_value_inr", _require_positive(self.cart_value_inr, "cart_value_inr")
        )
        object.__setattr__(
            self,
            "buyer_allowance_inr",
            _require_positive(self.buyer_allowance_inr, "buyer_allowance_inr"),
        )
        if not isinstance(self.is_stagnant, bool):
            raise InvalidDecisionInput("is_stagnant must be a boolean")


class ContextEncoder:
    def __init__(self, categories: tuple[str, ...]) -> None:
        if not categories:
            raise InvalidDecisionInput("at least one item category is required")
        for category in categories:
            if not isinstance(category, str) or not category.strip():
                raise InvalidDecisionInput("categories must be non-empty strings")
        if len(set(categories)) != len(categories):
            raise InvalidDecisionInput("categories must be unique")
        self._categories = tuple(categories)
        self._dimension = _STATIC_FEATURES + len(self._categories)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def categories(self) -> tuple[str, ...]:
        return self._categories

    def encode(self, context: DecisionContext) -> np.ndarray:
        if context.item_category not in self._categories:
            raise InvalidDecisionInput(
                f"unknown category {context.item_category!r}; known: {self._categories}"
            )
        features = np.zeros(self._dimension, dtype=float)
        features[0] = 1.0
        features[1] = (1.0 if not context.is_stagnant else 0.0) * (
            context.cart_value_inr / RUPEES_PER_UNIT
        )
        features[2] = context.buyer_allowance_inr / context.cart_value_inr
        features[3] = 1.0 if context.is_stagnant else 0.0
        features[4] = (context.cart_value_inr / RUPEES_PER_UNIT) * (
            (context.days_in_stock or 0) / 100.0
        )
        features[5] = (1.0 if context.is_stagnant else 0.0) * (
            context.cart_value_inr / RUPEES_PER_UNIT
        )
        offset = _STATIC_FEATURES + self._categories.index(context.item_category)
        features[offset] = 1.0
        return features
