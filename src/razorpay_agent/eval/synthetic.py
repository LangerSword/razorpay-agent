from __future__ import annotations

import random
from dataclasses import dataclass

CATEGORIES: tuple[str, ...] = ("apparel", "electronics")

BASE_COMPLETION_LOW = 0.75
BASE_COMPLETION_HIGH = 0.92
AVG_BASE_COMPLETION_PROB = (BASE_COMPLETION_LOW + BASE_COMPLETION_HIGH) / 2

DISCOUNT_LIFT_COEF = 0.35
DISCOUNT_NORM_CEILING_PERCENT = 30.0

CATEGORY_PRICE_SENSITIVITY = {"apparel": 1.4, "electronics": 0.5}

BUNDLE_RELEVANT_TAKE_RATE = 0.32
BUNDLE_IRRELEVANT_TAKE_RATE = 0.04
BUNDLE_MAX_SHARE_FOR_FULL_TAKE = 0.25

PUSHINESS_ABANDON_COEF = 0.15
PUSHINESS_IRRELEVANT_MULTIPLIER = 2.0


@dataclass(frozen=True)
class SimSession:
    index: int
    category: str
    cart_value_rupees: float
    allowance_rupees: float
    base_completion_prob: float


@dataclass(frozen=True)
class SimOffer:
    kind: str
    discount_percent: float | None = None
    bundle_price_rupees: float | None = None
    bundle_category_match: bool | None = None


def discount_completion_prob(session: SimSession, discount_percent: float) -> float:
    sensitivity = CATEGORY_PRICE_SENSITIVITY.get(session.category, 1.0)
    generosity = min(discount_percent / DISCOUNT_NORM_CEILING_PERCENT, 1.0)
    probability = session.base_completion_prob + DISCOUNT_LIFT_COEF * generosity * sensitivity
    return min(max(probability, 0.0), 0.99)


def bundle_outcome_probs(
    session: SimSession, price_rupees: float, category_match: bool
) -> tuple[float, float]:
    take_rate = BUNDLE_RELEVANT_TAKE_RATE if category_match else BUNDLE_IRRELEVANT_TAKE_RATE
    affordability = 1.0 - price_rupees / (
        BUNDLE_MAX_SHARE_FOR_FULL_TAKE * session.cart_value_rupees
    )
    affordability = min(max(affordability, 0.0), 1.0)
    p_take = take_rate * (0.5 + 0.5 * affordability)

    share = price_rupees / session.cart_value_rupees
    multiplier = 1.0 if category_match else PUSHINESS_IRRELEVANT_MULTIPLIER
    p_abandon = min(PUSHINESS_ABANDON_COEF * share * multiplier, 0.20)
    return p_take, p_abandon


class SimulatedBuyerModel:
    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def session(self, index: int) -> SimSession:
        return SimSession(
            index=index,
            category=self._rng.choice(CATEGORIES),
            cart_value_rupees=self._rng.uniform(800.0, 5000.0),
            allowance_rupees=self._rng.uniform(1500.0, 12000.0),
            base_completion_prob=self._rng.uniform(BASE_COMPLETION_LOW, BASE_COMPLETION_HIGH),
        )

    def respond_to_discount(self, session: SimSession, discount_percent: float) -> str:
        roll = self._rng.random()
        p_complete = discount_completion_prob(session, discount_percent)
        if roll < p_complete:
            return "completed_accepted"
        return "completed_declined"

    def respond_to_bundle(
        self, session: SimSession, price_rupees: float, category_match: bool
    ) -> str:
        p_take, p_abandon = bundle_outcome_probs(session, price_rupees, category_match)
        roll = self._rng.random()
        if roll < p_abandon:
            return "abandoned"
        if roll < p_abandon + p_take:
            return "completed_accepted"
        return "completed_declined"


def expected_net_revenue(session: SimSession, offer: SimOffer | None) -> float:
    if offer is None:
        return 0.0
    cart = session.cart_value_rupees
    baseline = session.base_completion_prob * cart
    if offer.kind == "discount":
        percent = float(offer.discount_percent)
        p = discount_completion_prob(session, percent)
        return p * cart * (1.0 - percent / 100.0) - baseline
    price = float(offer.bundle_price_rupees)
    p_take, p_abandon = bundle_outcome_probs(session, price, bool(offer.bundle_category_match))
    kept = 1.0 - p_abandon
    return kept * (cart + p_take * price) - baseline


def realized_net_revenue(
    session: SimSession,
    offer: SimOffer | None,
    response: str,
) -> float:
    if offer is None:
        return 0.0
    cart = session.cart_value_rupees
    baseline = session.base_completion_prob * cart
    if response == "abandoned":
        return -baseline
    if offer.kind == "discount":
        paid = cart * (1.0 - float(offer.discount_percent) / 100.0)
        return paid - baseline
    add_on = float(offer.bundle_price_rupees) if response == "completed_accepted" else 0.0
    return cart + add_on - baseline
