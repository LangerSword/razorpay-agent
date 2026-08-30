from __future__ import annotations

import random
from dataclasses import dataclass

from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.decision.arms import BundleArm, DiscountArm
from razorpay_agent.decision.context import ContextEncoder, DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.eval.storage import EvalStore
from razorpay_agent.eval.synthetic import (
    CATEGORIES,
    SimOffer,
    SimSession,
    SimulatedBuyerModel,
    expected_net_revenue,
    realized_net_revenue,
)
from razorpay_agent.gate.context import SessionContext
from razorpay_agent.gate.gate import RulePolicyGate, RulePolicyGateConfig

EVAL_DISCOUNT_ARMS = (5.0, 10.0, 15.0, 20.0)

BUNDLE_ITEM_CATEGORIES: dict[str, tuple[str, float]] = {
    "sku-socks": ("apparel", 499.0),
    "sku-charger": ("electronics", 1499.0),
}

FALLBACK_ITEM = "sku-socks"

HONESTY_NOTE = (
    "Offline validation on synthetic sessions with a legible probabilistic buyer model. "
    "This is evidence that the bandit-plus-gate machinery learns something coherent under "
    "controlled conditions - it is not a prediction of real-world revenue."
)


@dataclass(frozen=True)
class EvalArm:
    arm_id: str
    kind: str
    discount_percent: float | None = None
    bundle_item: str | None = None
    bundle_price_rupees: float | None = None


def build_eval_arms() -> tuple[EvalArm, ...]:
    arms = [
        EvalArm(f"d{int(percent)}", "discount", discount_percent=percent)
        for percent in EVAL_DISCOUNT_ARMS
    ]
    arms.extend(
        EvalArm(
            f"b_{item}",
            "bundle",
            bundle_item=item,
            bundle_price_rupees=price,
        )
        for item, (_, price) in BUNDLE_ITEM_CATEGORIES.items()
    )
    return tuple(arms)


def sim_offer_for_arm(arm: EvalArm, session: SimSession) -> SimOffer:
    if arm.kind == "discount":
        return SimOffer(kind="discount", discount_percent=arm.discount_percent)
    item_category = BUNDLE_ITEM_CATEGORIES[arm.bundle_item][0]
    return SimOffer(
        kind="bundle",
        bundle_price_rupees=arm.bundle_price_rupees,
        bundle_category_match=(item_category == session.category),
    )


def run_offline_validation(
    eval_store: EvalStore,
    seed: int = 7,
    n_sessions: int = 400,
    gate_config: RulePolicyGateConfig | None = None,
) -> dict:
    gate_config = gate_config or RulePolicyGateConfig(
        fallback_bundle_item=FALLBACK_ITEM,
        fallback_bundle_price=BUNDLE_ITEM_CATEGORIES[FALLBACK_ITEM][1],
    )
    rng = random.Random(seed)
    buyer = SimulatedBuyerModel(rng)
    gate = RulePolicyGate(gate_config)

    linucb_arms = [
        DiscountArm(arm.arm_id, float(arm.discount_percent))
        if arm.kind == "discount"
        else BundleArm(arm.arm_id, arm.bundle_item, float(arm.bundle_price_rupees))
        for arm in build_eval_arms()
    ]
    policy = LinUCBPolicy(linucb_arms, ContextEncoder(CATEGORIES), alpha=0.5)
    all_arms = build_eval_arms()

    steps: list[dict] = []
    bandit_rewards: list[float] = []
    fallback_rewards: list[float] = []
    proposals_made = 0
    proposals_compliant = 0
    cumulative_regret = 0.0

    for index in range(n_sessions):
        session = buyer.session(index)
        session_id = f"sim-{seed}-{index}"
        context = DecisionContext(
            session_id=session_id,
            target_sku=f"sim-sku-{index}",
            item_category=session.category,
            cart_value_inr=session.cart_value_rupees,
            buyer_allowance_inr=session.allowance_rupees,
        )
        best_expected = max(
            [expected_net_revenue(session, sim_offer_for_arm(arm, session)) for arm in all_arms]
            + [0.0]
        )

        bandit_step = _play_bandit_step(policy, gate, buyer, session, context, index, best_expected)
        steps.append(bandit_step)
        cumulative_regret += max(bandit_step["best_expected"] - bandit_step["chosen_expected"], 0.0)
        if bandit_step["arm_id"] is not None:
            proposals_made += 1
            if bandit_step["allowed"] and bandit_step["unmodified"]:
                proposals_compliant += 1
        bandit_rewards.append(bandit_step["reward"])

        fallback_step = _play_fallback_step(gate, buyer, session, index, best_expected)
        steps.append(fallback_step)
        fallback_rewards.append(fallback_step["reward"])

    bandit_mean = sum(bandit_rewards) / len(bandit_rewards)
    fallback_mean = sum(fallback_rewards) / len(fallback_rewards)
    compliance = proposals_compliant / proposals_made if proposals_made else 0.0

    eval_store.record_run(
        seed=seed,
        n_sessions=n_sessions,
        uplift_over_baseline=bandit_mean - fallback_mean,
        gate_compliance_rate=compliance,
        cumulative_regret=cumulative_regret,
        honesty_note=HONESTY_NOTE,
        steps=steps,
    )
    return {
        "bandit_mean_net_revenue": bandit_mean,
        "fallback_mean_net_revenue": fallback_mean,
        "uplift_over_baseline": bandit_mean - fallback_mean,
        "gate_compliance_rate": compliance,
        "cumulative_regret": cumulative_regret,
    }


def pretrain_policy(
    n_sessions: int,
    seed: int,
    gate_config: RulePolicyGateConfig | None = None,
) -> LinUCBPolicy:
    gate_config = gate_config or RulePolicyGateConfig(
        fallback_bundle_item=FALLBACK_ITEM,
        fallback_bundle_price=BUNDLE_ITEM_CATEGORIES[FALLBACK_ITEM][1],
    )
    rng = random.Random(seed)
    buyer = SimulatedBuyerModel(rng)
    gate = RulePolicyGate(gate_config)

    linucb_arms = [
        DiscountArm(arm.arm_id, float(arm.discount_percent))
        if arm.kind == "discount"
        else BundleArm(arm.arm_id, arm.bundle_item, float(arm.bundle_price_rupees))
        for arm in build_eval_arms()
    ]
    policy = LinUCBPolicy(linucb_arms, ContextEncoder(CATEGORIES), alpha=0.5)

    for index in range(n_sessions):
        session = buyer.session(index)
        context = DecisionContext(
            session_id=f"pretrain-{seed}-{index}",
            target_sku=f"sim-sku-{index}",
            item_category=session.category,
            cart_value_inr=session.cart_value_rupees,
            buyer_allowance_inr=session.allowance_rupees,
        )
        _play_bandit_step(policy, gate, buyer, session, context, index, 0.0)

    return policy


def _gate_context(session: SimSession, session_id: str) -> SessionContext:
    return SessionContext(
        session_id=session_id,
        cart_value_inr=session.cart_value_rupees,
        buyer_allowance_inr=session.allowance_rupees,
        already_offered=False,
    )


def _simulate_reward(buyer: SimulatedBuyerModel, session: SimSession, offer: SimOffer) -> float:
    if offer.kind == "discount":
        response = buyer.respond_to_discount(session, float(offer.discount_percent))
    else:
        response = buyer.respond_to_bundle(
            session, float(offer.bundle_price_rupees), bool(offer.bundle_category_match)
        )
    return realized_net_revenue(session, offer, response)


def _action_to_sim_offer(action: ProposedAction, session: SimSession) -> SimOffer | None:
    if action.action_type == "discount":
        return SimOffer(kind="discount", discount_percent=float(action.discount_percent))
    match = (
        action.bundle_item in BUNDLE_ITEM_CATEGORIES
        and BUNDLE_ITEM_CATEGORIES[action.bundle_item][0] == session.category
    )
    return SimOffer(
        kind="bundle",
        bundle_price_rupees=float(action.bundle_price),
        bundle_category_match=match,
    )


def _offers_equivalent(proposed: ProposedAction, final: ProposedAction) -> bool:
    if proposed.action_type != final.action_type:
        return False
    if proposed.action_type == "discount":
        return float(proposed.discount_percent) == float(final.discount_percent)
    return (
        proposed.bundle_item == final.bundle_item
        and float(proposed.bundle_price) == float(final.bundle_price)
    )


def _play_bandit_step(policy, gate, buyer, session, context, index, best_expected) -> dict:
    step = {
        "step_index": index * 2,
        "policy": "bandit",
        "session_category": session.category,
        "cart_value_rupees": session.cart_value_rupees,
        "arm_id": None,
        "allowed": False,
        "unmodified": False,
        "reward": 0.0,
        "chosen_expected": 0.0,
        "best_expected": best_expected,
    }

    arm_id, action = policy.propose_with_arm(context)
    if action is None:
        return step
    step["arm_id"] = arm_id

    decision = gate.evaluate(action, _gate_context(session, context.session_id))
    step["allowed"] = decision.allowed
    step["unmodified"] = decision.allowed and _offers_equivalent(action, decision.final_action)

    if not decision.allowed:
        policy.update(arm_id, context, 0.0)
        return step

    sent_offer = _action_to_sim_offer(decision.final_action, session)
    step["chosen_expected"] = expected_net_revenue(session, sent_offer)
    reward = _simulate_reward(buyer, session, sent_offer)
    step["reward"] = reward
    policy.update(arm_id, context, reward)
    return step


def _play_fallback_step(gate, buyer, session, index, best_expected) -> dict:
    fallback_action = ProposedAction(
        action_type="bundle_upsell",
        target=FALLBACK_ITEM,
        expected_uplift=0.0,
        confidence=0.0,
        source="fallback_rule",
        session_id=f"sim-fallback-{index}",
        bundle_item=FALLBACK_ITEM,
        bundle_price=BUNDLE_ITEM_CATEGORIES[FALLBACK_ITEM][1],
    )
    step = {
        "step_index": index * 2 + 1,
        "policy": "fallback",
        "session_category": session.category,
        "cart_value_rupees": session.cart_value_rupees,
        "arm_id": "b_" + FALLBACK_ITEM,
        "allowed": False,
        "unmodified": False,
        "reward": 0.0,
        "chosen_expected": 0.0,
        "best_expected": best_expected,
    }
    decision = gate.evaluate(fallback_action, _gate_context(session, fallback_action.session_id))
    step["allowed"] = decision.allowed
    step["unmodified"] = decision.allowed and _offers_equivalent(
        fallback_action, decision.final_action
    )
    if not decision.allowed:
        return step
    sent_offer = _action_to_sim_offer(decision.final_action, session)
    step["chosen_expected"] = expected_net_revenue(session, sent_offer)
    step["reward"] = _simulate_reward(buyer, session, sent_offer)
    return step
