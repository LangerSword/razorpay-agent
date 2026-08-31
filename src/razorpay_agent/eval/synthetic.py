from __future__ import annotations

import random
from dataclasses import dataclass

CATEGORIES: tuple[str, ...] = ("apparel", "electronics")

BASE_COMPLETION_LOW = 0.75
BASE_COMPLETION_HIGH = 0.92
AVG_BASE_COMPLETION_PROB = (BASE_COMPLETION_LOW + BASE_COMPLETION_HIGH) / 2

DISCOUNT_LIFT_COEF = 0.35
DISCOUNT_NORM_CEILING_PERCENT = 60.0

CATEGORY_PRICE_SENSITIVITY = {"apparel": 1.4, "electronics": 0.5}

BUNDLE_RELEVANT_TAKE_RATE = 0.32
BUNDLE_IRRELEVANT_TAKE_RATE = 0.04
BUNDLE_MAX_SHARE_FOR_FULL_TAKE = 0.25

PUSHINESS_ABANDON_COEF = 0.15
PUSHINESS_IRRELEVANT_MULTIPLIER = 2.0

# --- Inventory holding-cost assumption (documented, not tuned) ---
# Retail inventory carrying cost is commonly cited at ~20-30% of item value per
# annum. We assume 25% per annum, giving a daily carrying-cost rate. This single
# documented rate underpins BOTH clearance terms below (see architecture.md §4.7 /
# §4.8): the relief a clearance earns, and the penalty a non-clearance incurs.
# Both are grounded in a published range rather than picked to make demos look
# good. They are shared by the live reward path (checkout/offers.py) and the
# simulator so the audit metric, bandit update, and watchdog all agree.
ANNUAL_HOLDING_COST_RATE = 0.25
DAILY_HOLDING_COST_RATE = ANNUAL_HOLDING_COST_RATE / 365.0

# Secondary revenue bonus applied to stagnant sessions ON TOP of the clearance
# relief/penalty. Kept at 0.0 on purpose: the clearance objective must dominate
# so deeper discounts (which clear more reliably) strictly out-score shallow
# ones — otherwise margin favours a token 5% discount. A bundle attachment is a
# valid alternative because it also clears the unit (its add-on revenue is not
# credited, so it stays competitive without dwarfing the discount arms). Raise
# this toward ~0.1 if you want stale-stock offers to credit more revenue, at the
# cost of shallow discounts becoming more likely.
STALE_REVENUE_BONUS = 0.0

# For stale-stock clearance, only meaningful (deep) discounts are worth offering
# — a token 5-15% does not move dead inventory. The bundle upsell remains a valid
# alternative. Discount arm ids are "d<percent>". Defined here (not in offers.py)
# to avoid a circular import between eval.replay and checkout.offers.
CLEARANCE_MIN_DISCOUNT_PCT = 25.0


def is_deep_discount_arm(arm_id: str) -> bool:
    if not arm_id.startswith("d"):
        return False
    try:
        return float(arm_id[1:]) >= CLEARANCE_MIN_DISCOUNT_PCT
    except ValueError:
        return False


def clearance_relief_rupees(item_value_rupees: float, days_in_stock: int | None) -> float:
    """Avoided carrying cost of clearing a stagnant unit with `days_in_stock` left.

    Returns 0.0 when the item is not stagnant (days_in_stock is None/0). This is
    the single definition of the clearance relief term shared across sim and live.
    """
    if not days_in_stock or days_in_stock <= 0:
        return 0.0
    return DAILY_HOLDING_COST_RATE * float(item_value_rupees) * float(days_in_stock)


def carrying_cost_penalty_rupees(item_value_rupees: float) -> float:
    """One period (one day) of carrying cost for a stagnant unit that did not clear.

    Mirrors ANNUAL_HOLDING_COST_RATE — the same documented rate as clearance_relief.
    Used as the negative reward when a stagnant item fails to clear on a real
    proposal (declined or no-sale). Deliberately small: it is the cost of holding
    the unit one more period, never the full remaining lifetime. Returns a positive
    magnitude; the reward path negates it. Documented assumption, not tuned to a
    target, and applied only to observed session outcomes — never to a hypothetical
    no-traffic scenario the system has no visibility into.
    """
    return DAILY_HOLDING_COST_RATE * float(item_value_rupees)


@dataclass(frozen=True)
class SimSession:
    index: int
    category: str
    cart_value_rupees: float
    allowance_rupees: float
    base_completion_prob: float
    is_stagnant: bool = False
    days_in_stock: int = 0


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
        category = self._rng.choice(CATEGORIES)
        cart_value_rupees = self._rng.uniform(800.0, 5000.0)
        allowance_rupees = self._rng.uniform(1500.0, 12000.0)
        # ~30% of sessions model inventory that is not moving: low base completion
        # probability and many days in stock. Stagnancy is a structural property of
        # the (simulated) merchant data, never inferred by the bandit.
        if self._rng.random() < 0.30:
            return SimSession(
                index=index,
                category=category,
                cart_value_rupees=cart_value_rupees,
                allowance_rupees=allowance_rupees,
                base_completion_prob=self._rng.uniform(0.30, 0.55),
                is_stagnant=True,
                days_in_stock=self._rng.randint(45, 150),
            )
        return SimSession(
            index=index,
            category=category,
            cart_value_rupees=cart_value_rupees,
            allowance_rupees=allowance_rupees,
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
        value = p * cart * (1.0 - percent / 100.0) - baseline
        p_done = p
    else:
        price = float(offer.bundle_price_rupees)
        p_take, p_abandon = bundle_outcome_probs(session, price, bool(offer.bundle_category_match))
        kept = 1.0 - p_abandon
        value = kept * (cart + p_take * price) - baseline
        p_done = kept
    if session.is_stagnant:
        # Stagnant clearance objective (architecture.md §4.7/§4.8), extended so the
        # bandit is free to choose EITHER tactic that clears the dead-stock unit:
        # a deep discount OR a bundle attachment. The clearance relief/penalty
        # DOMINATES (so deeper discounts, which clear more reliably, out-score
        # shallow ones); the ordinary revenue is only a small secondary bonus
        # (STALE_REVENUE_BONUS). For a bundle, only the stale unit's own sale is
        # credited — not the add-on — so a bundle stays a valid alternative
        # without dwarfing the discount arms.
        relief = clearance_relief_rupees(cart, session.days_in_stock)
        penalty = carrying_cost_penalty_rupees(cart)
        clearance = p_done * relief - (1.0 - p_done) * penalty
        if offer.kind == "discount":
            revenue = value
        else:
            revenue = cart - baseline
        value = clearance + STALE_REVENUE_BONUS * revenue
    return value


def realized_net_revenue(
    session: SimSession,
    offer: SimOffer | None,
    response: str,
) -> float:
    if offer is None:
        return 0.0
    cart = session.cart_value_rupees
    baseline = session.base_completion_prob * cart
    if session.is_stagnant:
        # Clearance objective for dead stock (see expected_net_revenue): clearance
        # relief/penalty dominates, with a small secondary revenue bonus. For a
        # bundle, only the stale unit's own sale is credited (add-on excluded) so
        # it remains an alternative without dwarfing discounts. Applies only to
        # observed session outcomes — never to a hypothetical no-traffic scenario.
        relief = clearance_relief_rupees(cart, session.days_in_stock)
        penalty = carrying_cost_penalty_rupees(cart)
        clearance = relief if response == "completed_accepted" else -penalty
        if response == "abandoned":
            revenue = -baseline
        elif offer.kind == "discount":
            paid = cart * (1.0 - float(offer.discount_percent) / 100.0)
            revenue = paid - baseline
        else:
            revenue = cart - baseline
        return clearance + STALE_REVENUE_BONUS * revenue
    if response == "abandoned":
        return -baseline
    if offer.kind == "discount":
        paid = cart * (1.0 - float(offer.discount_percent) / 100.0)
        value = paid - baseline
    else:
        add_on = float(offer.bundle_price_rupees) if response == "completed_accepted" else 0.0
        value = cart + add_on - baseline
    return value
