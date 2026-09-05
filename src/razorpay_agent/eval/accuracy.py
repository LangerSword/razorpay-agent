"""Accuracy grading for both agents' reasoning and decisions.

The merchant bandit has a rigorous eval harness (:mod:`eval.replay`) — but the
*buyer's* verdicts and *both agents' reasoning traces* have never been graded
automatically.  This module fills that gap:

* :func:`grade_buyer_verdict` — compares a buyer's ACCEPT/DECLINE against the
  optimal verdict for the offer and the buyer's stated criteria.
* :func:`grade_merchant_reasoning` — checks a reasoning trace for arm
  identification, gate awareness, limit accuracy, and verdict correctness.
* :func:`run_buyer_accuracy_eval` — grades the buyer across a sweep of offers.
* :func:`run_merchant_reasoning_eval` — grades the merchant reasoner across
  contexts.

All grading is deterministic and keyless (no LLM needed) — the same standard as
the bandit's simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Buyer verdict grading
# ---------------------------------------------------------------------------


def _optimal_verdict(
    offer_type: str,
    discount_percent: float | None,
    add_on_price_inr: float | None,
    cart_value_inr: float,
    min_discount_percent: float,
    max_add_on_share: float,
) -> str:
    """Compute the optimal verdict for an offer given the buyer's criteria.

    For discounts: ACCEPT iff ``discount_percent >= min_discount_percent``.
    For bundles: ACCEPT iff ``add_on_price / cart_value <= max_add_on_share``.
    """
    if offer_type == "discount":
        if discount_percent is None:
            return "decline"
        return "accept" if discount_percent >= min_discount_percent else "decline"
    if offer_type == "bundle":
        if add_on_price_inr is None or cart_value_inr <= 0:
            return "decline"
        share = add_on_price_inr / cart_value_inr
        return "accept" if share <= max_add_on_share else "decline"
    return "decline"


@dataclass(frozen=True)
class BuyerGrade:
    correct: bool
    expected: str  # "accept" | "decline"
    actual: str  # "accept" | "decline"
    offer_type: str
    detail: str


def grade_buyer_verdict(
    verdict: str,
    offer_type: str,
    *,
    discount_percent: float | None = None,
    add_on_price_inr: float | None = None,
    cart_value_inr: float = 1.0,
    min_discount_percent: float = 5.0,
    max_add_on_share: float = 0.25,
) -> BuyerGrade:
    """Grade a buyer's verdict against the optimal decision.

    Returns a :class:`BuyerGrade` with ``correct=True`` when the verdict matches
    the optimal verdict given the buyer's stated criteria.
    """
    verdict_clean = "accept" if verdict.lower() in ("accept", "approve") else "decline"
    expected = _optimal_verdict(
        offer_type,
        discount_percent,
        add_on_price_inr,
        cart_value_inr,
        min_discount_percent,
        max_add_on_share,
    )
    if expected == verdict_clean:
        return BuyerGrade(
            correct=True,
            expected=expected,
            actual=verdict_clean,
            offer_type=offer_type,
            detail=f"{offer_type} offer correctly {verdict_clean}ed",
        )
    return BuyerGrade(
        correct=False,
        expected=expected,
        actual=verdict_clean,
        offer_type=offer_type,
        detail=(
            f"Expected {expected} but got {verdict_clean}: "
            f"{offer_type} offer (discount={discount_percent}%, "
            f"add_on=₹{add_on_price_inr}, cart=₹{cart_value_inr}, "
            f"min_disc={min_discount_percent}%, max_share={max_add_on_share:.0%})"
        ),
    )


# ---------------------------------------------------------------------------
# Merchant reasoning grading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MerchantGrade:
    correct: bool
    arm_identified: bool
    gate_aware: bool
    limits_accurate: bool
    verdict_correct: bool
    detail: str


def grade_merchant_reasoning(
    reasoning_text: str,
    *,
    bandit_arm_id: str | None = None,
    bandit_discount_percent: float | None = None,
    gate_allowed: bool = True,
    gate_capped: bool = False,
    gate_reason: str = "",
    max_discount_percent: float = 15.0,
    max_discount_rupee_cap: float = 300.0,
) -> MerchantGrade:
    """Grade a merchant reasoning trace for accuracy.

    Checks four independent dimensions:

    * ``arm_identified`` — does the reasoning mention the discount percent the
      bandit actually proposed?
    * ``gate_aware`` — does it correctly note whether the gate allowed,
      capped, or rejected?
    * ``limits_accurate`` — does it cite the correct policy limits?
    * ``verdict_correct`` — does the verdict (APPROVE/REJECT/REVIEW) match the
      gate decision?
    """
    text = reasoning_text.lower()

    # --- Arm identification ---
    arm_identified = False
    if bandit_discount_percent is not None:
        # Check if the exact discount percent is mentioned
        if f"{bandit_discount_percent:g}%" in reasoning_text or f"{bandit_discount_percent}%" in reasoning_text:
            arm_identified = True
        elif f"{int(bandit_discount_percent)}%" in reasoning_text:
            arm_identified = True
    elif bandit_arm_id and bandit_arm_id.startswith("b_"):
        # Bundle arm — check if bundle is mentioned (item name or generic "bundle add-on")
        item = bandit_arm_id[2:]
        if item in text or "bundle add-on" in text:
            arm_identified = True

    # --- Gate awareness ---
    gate_aware = False
    if gate_allowed and not gate_capped:
        if "allowed" in text or "within" in text or "approve" in text:
            gate_aware = True
    elif gate_capped:
        if "capped" in text or "cap" in text:
            gate_aware = True
    elif not gate_allowed:
        if "reject" in text or "blocked" in text or "over" in text:
            gate_aware = True

    # --- Limits accuracy ---
    limits_accurate = False
    if (
        f"{int(max_discount_percent)}%" in reasoning_text
        or f"{max_discount_percent:g}%" in reasoning_text
    ):
        limits_accurate = True
    elif f"{int(max_discount_rupee_cap)}" in reasoning_text:
        limits_accurate = True
    elif "15%" in reasoning_text or "300" in reasoning_text:
        limits_accurate = True

    # --- Verdict correctness ---
    verdict_correct = False
    has_approve = "verdict: approve" in text or "verdict: app" in text
    has_reject = "verdict: reject" in text or "verdict: rej" in text
    has_review = "verdict: review" in text
    if gate_allowed and not gate_capped:
        verdict_correct = has_approve
    elif gate_capped:
        # Capped = still allowed, so approve is correct
        verdict_correct = has_approve or has_review
    elif not gate_allowed:
        verdict_correct = has_reject or has_review

    correct = arm_identified and gate_aware and limits_accurate and verdict_correct

    detail_parts = []
    if not arm_identified:
        detail_parts.append("failed to identify the bandit arm")
    if not gate_aware:
        detail_parts.append("gate action not correctly noted")
    if not limits_accurate:
        detail_parts.append("policy limits not accurately cited")
    if not verdict_correct:
        detail_parts.append("verdict does not match gate decision")
    detail = "; ".join(detail_parts) if detail_parts else "all dimensions correct"

    return MerchantGrade(
        correct=correct,
        arm_identified=arm_identified,
        gate_aware=gate_aware,
        limits_accurate=limits_accurate,
        verdict_correct=verdict_correct,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Sweep evaluators
# ---------------------------------------------------------------------------


@dataclass
class BuyerAccuracySummary:
    total: int
    correct: int
    accuracy: float
    by_offer_type: dict[str, dict[str, float]] = field(default_factory=dict)
    failures: list[BuyerGrade] = field(default_factory=list)


def run_buyer_accuracy_eval(
    llm: Any,
    *,
    min_discount_percent: float = 5.0,
    max_add_on_share: float = 0.25,
    memory: Any | None = None,
) -> BuyerAccuracySummary:
    """Run the buyer reasoner across a sweep of offers and grade each verdict.

    Uses the ``buyer.reasoning_agent.evaluate_offer`` function with synthetic
    ACP session payloads.  Keyless — works with StubBackend and real LLMs alike.
    """
    from razorpay_agent.buyer.reasoning_agent import PurchaseMemory, evaluate_offer

    if memory is None:
        memory = PurchaseMemory()

    scenarios = [
        # (offer_type, discount, add_on_price, cart_value, expected)
        ("discount", 3.0, None, 2499.0, "decline"),
        ("discount", 5.0, None, 2499.0, "accept"),
        ("discount", 10.0, None, 2499.0, "accept"),
        ("discount", 15.0, None, 2499.0, "accept"),
        ("discount", 1.0, None, 999.0, "decline"),
        ("discount", 20.0, None, 9998.0, "accept"),
        ("bundle", None, 200.0, 2499.0, "accept"),
        ("bundle", None, 600.0, 2499.0, "decline"),
        ("bundle", None, 499.0, 4998.0, "accept"),
        ("bundle", None, 500.0, 1999.0, "decline"),
    ]

    grades: list[BuyerGrade] = []
    by_type: dict[str, list[bool]] = {}

    for offer_type, disc_pct, add_on_price, cart_val, _expected in scenarios:
        # Build a synthetic ACP session payload
        if offer_type == "discount":
            base_paise = int(cart_val * 100)
            disc_paise = int(base_paise * disc_pct / 100)
            session = {
                "target_sku": "sku-hoodie",
                "suggested_add_on": None,
                "line_items": [
                    {
                        "item": {"id": "sku-hoodie", "quantity": 1},
                        "base_amount": base_paise,
                        "discount": disc_paise,
                        "total": base_paise - disc_paise,
                    }
                ],
                "totals": [
                    {"type": "items_base_amount", "amount": base_paise},
                    {"type": "items_discount", "amount": -disc_paise},
                    {"type": "subtotal", "amount": base_paise - disc_paise},
                    {"type": "total", "amount": base_paise - disc_paise},
                ],
            }
        else:  # bundle
            base_paise = int(cart_val * 100)
            ao_paise = int(add_on_price * 100)
            session = {
                "target_sku": "sku-hoodie",
                "suggested_add_on": {
                    "item_id": "sku-socks",
                    "unit_amount": ao_paise,
                    "currency": "inr",
                },
                "line_items": [
                    {
                        "item": {"id": "sku-hoodie", "quantity": 1},
                        "base_amount": base_paise,
                        "discount": 0,
                        "total": base_paise,
                    }
                ],
                "totals": [
                    {"type": "items_base_amount", "amount": base_paise},
                    {"type": "add_on", "amount": ao_paise},
                    {"type": "subtotal", "amount": base_paise + ao_paise},
                    {"type": "total", "amount": base_paise + ao_paise},
                ],
            }

        result = evaluate_offer(
            llm=llm,
            session=session,
            cart_value_inr=cart_val,
            buyer_allowance_inr=100_000.0,
            memory=memory,
            min_discount_percent=min_discount_percent,
            max_add_on_share=max_add_on_share,
        )

        grade = grade_buyer_verdict(
            verdict=result.verdict,
            offer_type=offer_type,
            discount_percent=disc_pct,
            add_on_price_inr=add_on_price,
            cart_value_inr=cart_val,
            min_discount_percent=min_discount_percent,
            max_add_on_share=max_add_on_share,
        )
        grades.append(grade)
        by_type.setdefault(offer_type, []).append(grade.correct)

    correct_count = sum(1 for g in grades if g.correct)
    by_offer_type: dict[str, dict[str, float]] = {}
    for offer_type, results in by_type.items():
        by_offer_type[offer_type] = {
            "total": len(results),
            "correct": sum(results),
            "accuracy": sum(results) / len(results) if results else 0.0,
        }

    return BuyerAccuracySummary(
        total=len(grades),
        correct=correct_count,
        accuracy=correct_count / len(grades) if grades else 0.0,
        by_offer_type=by_offer_type,
        failures=[g for g in grades if not g.correct],
    )


@dataclass
class MerchantReasoningSummary:
    total: int
    correct: int
    accuracy: float
    arm_identification_rate: float
    gate_awareness_rate: float
    limits_accuracy_rate: float
    verdict_accuracy_rate: float
    failures: list[MerchantGrade] = field(default_factory=list)


def run_merchant_reasoning_eval(
    llm: Any,
    *,
    gate_config: Any | None = None,
) -> MerchantReasoningSummary:
    """Run the merchant reasoner across contexts and grade each trace.

    Uses the ``reasoning.agent.ReasoningAgent`` with synthetic contexts.
    Keyless — works with StubBackend and real LLMs alike.
    """
    from razorpay_agent.checkout.catalog import DEMO_CATALOG
    from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
    from razorpay_agent.gate.gate import RulePolicyGateConfig
    from razorpay_agent.reasoning.agent import ReasoningAgent
    from razorpay_agent.reasoning.store import ReasoningStore
    from razorpay_agent.reasoning.tools import ReasoningDeps
    from razorpay_agent.server import fresh_policy

    if gate_config is None:
        gate_config = RulePolicyGateConfig(
            fallback_bundle_item="sku-socks",
            fallback_bundle_price=499.0,
        )

    cats = tuple(sorted({p.category for p in DEMO_CATALOG}))
    policy = fresh_policy(cats)
    deps = ReasoningDeps(
        DEMO_CATALOG,
        policy,
        gate_config,
        regimen_graph=CoPurchaseGraph.from_catalog(DEMO_CATALOG),
    )
    agent = ReasoningAgent(
        llm=llm,
        deps=deps,
        store=ReasoningStore(":memory:"),
    )

    scenarios = [
        # (name, bandit_action, gate_decision, cart_value, expected_capped)
        (
            "normal_allowed",
            {"action_type": "discount", "discount_percent": 10},
            {"allowed": True, "reason": "10% discount within all limits"},
            2499.0,
            False,
        ),
        (
            "capped_by_rupee",
            {"action_type": "discount", "discount_percent": 10},
            {"allowed": True, "reason": "rupee discount capped at 300.00 (3.0% of cart)", "final_discount_percent": 3.0},
            9998.0,
            True,  # 10% of 9998 = ~999 > 300 cap → capped to 3%
        ),
        (
            "deep_discount_rejected",
            {"action_type": "discount", "discount_percent": 35},
            {"allowed": False, "reason": "rejected: 35% exceeds 15% max discount limit"},
            3999.0,
            False,
        ),
        (
            "bundle_allowed",
            {"action_type": "bundle_upsell", "bundle_item": "sku-socks", "bundle_price": 499.0},
            {"allowed": True, "reason": "bundle within share limit and buyer allowance"},
            4998.0,
            False,
        ),
    ]

    grades: list[MerchantGrade] = []

    for name, bandit_action, gate_decision, cart_val, _expected_capped in scenarios:
        result = agent.reason(
            session_id=f"eval-{name}",
            target_sku="sku-hoodie",
            item_category="apparel",
            cart_value_inr=cart_val,
            buyer_allowance_inr=100_000.0,
            bandit_action=bandit_action,
            gate_decision=gate_decision,
        )

        discount_pct = (
            float(bandit_action["discount_percent"])
            if bandit_action["action_type"] == "discount"
            else None
        )
        arm_id = None
        if bandit_action["action_type"] == "bundle_upsell":
            arm_id = f"b_{bandit_action['bundle_item']}"

        grade = grade_merchant_reasoning(
            reasoning_text=result.final_text,
            bandit_arm_id=arm_id,
            bandit_discount_percent=discount_pct,
            gate_allowed=gate_decision.get("allowed", True),
            gate_capped=_expected_capped,
            max_discount_percent=int(gate_config.max_discount_percent),
            max_discount_rupee_cap=int(gate_config.max_discount_rupee_cap),
        )
        grades.append(grade)

    correct_count = sum(1 for g in grades if g.correct)
    arm_count = sum(1 for g in grades if g.arm_identified)
    gate_count = sum(1 for g in grades if g.gate_aware)
    limits_count = sum(1 for g in grades if g.limits_accurate)
    verdict_count = sum(1 for g in grades if g.verdict_correct)
    n = len(grades)

    return MerchantReasoningSummary(
        total=n,
        correct=correct_count,
        accuracy=correct_count / n if n else 0.0,
        arm_identification_rate=arm_count / n if n else 0.0,
        gate_awareness_rate=gate_count / n if n else 0.0,
        limits_accuracy_rate=limits_count / n if n else 0.0,
        verdict_accuracy_rate=verdict_count / n if n else 0.0,
        failures=[g for g in grades if not g.correct],
    )
