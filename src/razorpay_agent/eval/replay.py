from __future__ import annotations

import random
from dataclasses import dataclass

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.decision.arms import Arm, BundleArm, DiscountArm
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.decision.context import ContextEncoder, DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.eval.storage import EvalStore
from razorpay_agent.eval.synthetic import (
    CATEGORIES,
    SimOffer,
    SimSession,
    SimulatedBuyerModel,
    expected_net_revenue,
    is_deep_discount_arm,
    realized_net_revenue,
)
from razorpay_agent.gate.context import SessionContext
from razorpay_agent.gate.gate import RulePolicyGate, RulePolicyGateConfig

# Documented-prior regimen graph the simulator reads bundle relevance from. The reward
# formula shape is unchanged; only the source of the "is this bundle relevant?" flag
# moves from naive category equality to this graph lookup.
_DEFAULT_REGIMEN = CoPurchaseGraph.from_catalog(DEMO_CATALOG)

EVAL_DISCOUNT_ARMS = (5.0, 10.0, 15.0, 20.0, 25.0, 35.0, 40.0)

# Seeded round-robin warmup: force every arm an equal, known number of times at the
# start of pretraining so no arm is starved of samples (LinUCB's exploration bonus is
# swamped by the rupee-scale reward, which otherwise leaves deep arms untrained). Each
# arm gets WARMUP_PER_ARM samples spread across both normal and stagnant contexts.
WARMUP_PER_ARM = 250

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


def sim_offer_for_arm(
    arm: EvalArm, session: SimSession, regimen_graph: CoPurchaseGraph | None = None
) -> SimOffer:
    if arm.kind == "discount":
        return SimOffer(kind="discount", discount_percent=arm.discount_percent)
    regimen_graph = regimen_graph or _DEFAULT_REGIMEN
    item_category = BUNDLE_ITEM_CATEGORIES[arm.bundle_item][0]
    relevant = regimen_graph.relevant_categories(session.category)
    return SimOffer(
        kind="bundle",
        bundle_price_rupees=arm.bundle_price_rupees,
        bundle_category_match=item_category in relevant,
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
            is_stagnant=session.is_stagnant,
            days_in_stock=session.days_in_stock,
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
    temperature: float = 0.0,
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

    # Stratified round-robin warmup: each arm is forced WARMUP_PER_ARM times in
    # EACH context class (normal and stagnant), so every arm gets enough
    # samples of the rarer stagnant context to learn its completion-probability
    # signal. The plan is INTERLEAVED arm-by-arm (not arm-ordered) so that any
    # prefix of the training run still forces every arm at least once -- otherwise,
    # with a long per-arm count and fewer total sessions, the later arms would
    # never be forced and would stay untrained. This is an exploration guarantee,
    # not a reward tweak.
    warmup_plan: list[tuple["Arm", bool]] = []
    for _ in range(WARMUP_PER_ARM):
        for arm in linucb_arms:
            for want_stagnant in (False, True):
                if (
                    want_stagnant
                    and isinstance(arm, DiscountArm)
                    and not is_deep_discount_arm(arm.arm_id)
                ):
                    # Token discounts are never offered for stagnant sessions, so
                    # do not force-train them there.
                    continue
                warmup_plan.append((arm, want_stagnant))
    warmup_total = min(len(warmup_plan), n_sessions)

    gen_index = n_sessions

    def session_of_class(want_stagnant: bool) -> SimSession:
        nonlocal gen_index
        while True:
            session = buyer.session(gen_index)
            gen_index += 1
            if session.is_stagnant == want_stagnant:
                return session

    for index in range(n_sessions):
        session = buyer.session(index)
        context = DecisionContext(
            session_id=f"pretrain-{seed}-{index}",
            target_sku=f"sim-sku-{index}",
            item_category=session.category,
            cart_value_inr=session.cart_value_rupees,
            buyer_allowance_inr=session.allowance_rupees,
            is_stagnant=session.is_stagnant,
            days_in_stock=session.days_in_stock,
        )
        if index < warmup_total:
            forced_arm, want_stagnant = warmup_plan[index]
            warmup_session = session_of_class(want_stagnant)
            warmup_context = DecisionContext(
                session_id=f"pretrain-{seed}-w{index}",
                target_sku=f"sim-sku-w{index}",
                item_category=warmup_session.category,
                cart_value_inr=warmup_session.cart_value_rupees,
                buyer_allowance_inr=warmup_session.allowance_rupees,
                is_stagnant=warmup_session.is_stagnant,
                days_in_stock=warmup_session.days_in_stock,
            )
            forced = (forced_arm.arm_id, _action_for_arm(forced_arm, warmup_context))
            _play_bandit_step(
                policy, gate, buyer, warmup_session, warmup_context, index, 0.0, forced=forced
            )
        else:
            _play_bandit_step(
                policy, gate, buyer, session, context, index, 0.0,
                forced=None, temperature=temperature, rng=buyer._rng,
            )

    return policy


def _gate_context(session: SimSession, session_id: str) -> SessionContext:
    return SessionContext(
        session_id=session_id,
        cart_value_inr=session.cart_value_rupees,
        buyer_allowance_inr=session.allowance_rupees,
        already_offered=False,
        is_stagnant=session.is_stagnant,
    )


def _simulate_reward(buyer: SimulatedBuyerModel, session: SimSession, offer: SimOffer) -> float:
    if offer.kind == "discount":
        response = buyer.respond_to_discount(session, float(offer.discount_percent))
    else:
        response = buyer.respond_to_bundle(
            session, float(offer.bundle_price_rupees), bool(offer.bundle_category_match)
        )
    return realized_net_revenue(session, offer, response)


def _action_to_sim_offer(
    action: ProposedAction,
    session: SimSession,
    regimen_graph: CoPurchaseGraph | None = None,
) -> SimOffer | None:
    if action.action_type == "discount":
        return SimOffer(kind="discount", discount_percent=float(action.discount_percent))
    regimen_graph = regimen_graph or _DEFAULT_REGIMEN
    match = False
    if action.bundle_item in BUNDLE_ITEM_CATEGORIES:
        item_category = BUNDLE_ITEM_CATEGORIES[action.bundle_item][0]
        match = item_category in regimen_graph.relevant_categories(session.category)
    return SimOffer(
        kind="bundle",
        bundle_price_rupees=float(action.bundle_price),
        bundle_category_match=match,
    )


def _action_for_arm(arm: "Arm", context: DecisionContext) -> ProposedAction:
    """Build the ProposedAction a bandit arm would emit (used for forced warmup)."""
    if isinstance(arm, DiscountArm):
        return ProposedAction(
            action_type="discount",
            target=context.target_sku,
            expected_uplift=0.0,
            confidence=0.0,
            source="linucb_bandit",
            session_id=context.session_id,
            discount_percent=float(arm.discount_percent),
        )
    return ProposedAction(
        action_type="bundle_upsell",
        target=arm.bundle_item,
        expected_uplift=0.0,
        confidence=0.0,
        source="linucb_bandit",
        session_id=context.session_id,
        bundle_item=arm.bundle_item,
        bundle_price=float(arm.bundle_price),
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


def _play_bandit_step(policy, gate, buyer, session, context, index, best_expected, forced=None, temperature=0.0, rng=None) -> dict:
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

    if forced is not None:
        arm_id, action = forced
    else:
        # Softmax sampling (temperature > 0) for exploration, else greedy
        # argmax. For stagnant sessions, restrict discount arms to the
        # deeper ones (a token discount does not clear dead stock), with
        # the bundle upsell kept as a valid alternative.
        allowed = None
        if session.is_stagnant:
            allowed = [
                aid
                for aid in policy.arm_ids
                if aid.startswith("b") or is_deep_discount_arm(aid)
            ]
        arm_id, action = policy.propose_with_arm(
            context, allowed_arm_ids=allowed, temperature=temperature, rng=rng
        )
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
