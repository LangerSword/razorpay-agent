from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone

from razorpay_agent.audit import AuditStore
from razorpay_agent.core.currency import INR
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.sessions import CheckoutSessionState, SessionRepository
from razorpay_agent.eval.replay import BUNDLE_ITEM_CATEGORIES, FALLBACK_ITEM
from razorpay_agent.eval.synthetic import SimSession, SimulatedBuyerModel
from razorpay_agent.gate import RulePolicyGateConfig
from razorpay_agent.server import (
    DEFAULT_DB_PATH,
    PRETRAINED_BANDIT_PATH,
    build_policy,
    build_watchdog,
)
from razorpay_agent.watchdog.sabotage import SabotagedPolicy

HEALTHY_SESSIONS = 35
SABOTAGE_SESSIONS = 34
POST_DEMOTION_SESSIONS = 3
RECOVERY_SESSIONS = 2


def make_session(repo: SessionRepository, index: int, category: str, cart_paise: int):
    state = CheckoutSessionState(
        id=f"wd-{index}",
        status="ready_for_payment",
        currency=INR,
        items=[{"product_id": "sku-hoodie", "quantity": 1}],
        allowance_max_paise=10_000_000,
        allowance_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    repo.save(state)
    return state


def run_sessions(pipeline, repo, buyer_model, rng, start_index: int, count: int, runner):
    for offset in range(count):
        index = start_index + offset
        category = rng.choice(("apparel", "home"))
        cart_rupees = rng.uniform(800.0, 5000.0)
        cart_paise = int(cart_rupees * 100)

        state = make_session(repo, index, category, cart_paise)
        offer = pipeline.propose_for_session(state, cart_paise, "sku-hoodie", category)

        decision_line = f"session {index}: proposed [{offer.proposed_action.source}]"
        sim_session = _sim_session_stub(category, cart_rupees)

        if not offer.gate_decision.allowed:
            decision_line += f" -> GATE REJECTED ({offer.gate_decision.reason.split(';')[0]})"
            runner.say("  " + decision_line)
            continue

        final_action = offer.gate_decision.final_action
        if final_action.action_type == "discount":
            sent_percent = float(final_action.discount_percent)
            response = buyer_model.respond_to_discount(sim_session, sent_percent)
        else:
            match = (
                final_action.bundle_item in BUNDLE_ITEM_CATEGORIES
                and BUNDLE_ITEM_CATEGORIES[final_action.bundle_item][0] == category
            )
            response = buyer_model.respond_to_bundle(
                sim_session, float(final_action.bundle_price), match
            )

        if response == "abandoned":
            pipeline.resolve_declined(state, "simulated buyer abandoned checkout")
            decision_line += " -> buyer abandoned after offer"
        elif response == "completed_accepted":
            paid_total = (
                int(cart_paise * (1 - float(final_action.discount_percent) / 100))
                if final_action.action_type == "discount"
                else cart_paise + int(float(final_action.bundle_price) * 100)
            )
            pipeline.resolve_accepted(state, paid_total, cart_paise)
            decision_line += " -> accepted & completed"
        else:
            pipeline.resolve_declined(state, "buyer completed without engaging offer")
            decision_line += " -> declined, bought anyway"
        runner.say("  " + decision_line)


def _sim_session_stub(category: str, cart_rupees: float):
    return SimSession(
        index=0,
        category=category,
        cart_value_rupees=cart_rupees,
        allowance_rupees=12000.0,
        base_completion_prob=0.85,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="watchdog demotion/re-promotion walkthrough")
    parser.add_argument("--seed", type=int, default=99)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    buyer_model = SimulatedBuyerModel(random.Random(args.seed + 1))

    policy, is_warm = build_policy(PRETRAINED_BANDIT_PATH)
    watchdog, events = build_watchdog(DEFAULT_DB_PATH)

    repo = SessionRepository()
    audit_store = AuditStore(":memory:")
    healthy_pipeline = OfferPipeline(policy, RulePolicyGateConfig(FALLBACK_ITEM, 499.0), audit_store, watchdog=watchdog)

    def say(text: str = "") -> None:
        print(text)

    class Echo:
        def __init__(self, fn):
            self.say = fn

    echo = Echo(say)

    say(f"[watchdog-demo] policy {'warm-started' if is_warm else 'COLD'}; "
        f"baseline revenue/decision {watchdog.status()['baseline']['net_revenue_per_decision']:.2f}, "
        f"compliance {watchdog.status()['baseline']['gate_compliance_rate']:.0%}")

    say(f"\n=== PHASE 1: {HEALTHY_SESSIONS} healthy sessions ===")
    run_sessions(healthy_pipeline, repo, buyer_model, rng, 0, HEALTHY_SESSIONS, echo)
    say(f"  watchdog status: demoted={watchdog.demoted}; rolling={_rolling(watchdog)}")

    sabotaged = SabotagedPolicy(policy, "b_sku-charger")
    sabotage_pipeline = OfferPipeline(
        sabotaged, RulePolicyGateConfig(FALLBACK_ITEM, 499.0), audit_store, watchdog=watchdog
    )

    say(f"\n=== PHASE 2: SABOTAGE ON for {SABOTAGE_SESSIONS} sessions (always proposes oversized bundle) ===")
    run_sessions(sabotage_pipeline, repo, buyer_model, rng, HEALTHY_SESSIONS, SABOTAGE_SESSIONS, echo)
    say(f"  watchdog status: demoted={watchdog.demoted}")
    if watchdog.demoted:
        say(f"  reason: {watchdog.demotion_reason}")

    say(f"\n=== PHASE 3: {POST_DEMOTION_SESSIONS} sessions under RULE-ONLY fallback ===")
    run_sessions(sabotage_pipeline, repo, buyer_model, rng,
                 HEALTHY_SESSIONS + SABOTAGE_SESSIONS, POST_DEMOTION_SESSIONS, echo)
    say("  every proposal above sources 'fallback_rule'; bandit not consulted.")

    say("\n=== PHASE 4: operator re-promotion (manual switch) ===")
    watchdog.promote("offline revalidation passed; promoting bandit")
    run_sessions(healthy_pipeline, repo, buyer_model, rng,
                 HEALTHY_SESSIONS + SABOTAGE_SESSIONS + POST_DEMOTION_SESSIONS,
                 RECOVERY_SESSIONS, echo)

    say("\n=== system events (durable record) ===")
    for event in events.recent(limit=5, component="watchdog"):
        detail = event["detail"][:80]
        say(f"  [{event['timestamp'][11:19]}] {event['event_type']}: {detail}")


def _rolling(watchdog) -> str:
    rolling = watchdog.status()["rolling"]
    mean_rev = rolling["mean_net_revenue"]
    comp = rolling["compliance_rate"]
    rev_text = f"{mean_rev:.1f}" if mean_rev is not None else "-"
    comp_text = f"{comp:.0%}" if comp is not None else "-"
    return f"mean_net_revenue={rev_text} over {rolling['reward_samples']}, compliance={comp_text} over {rolling['compliance_samples']}"


if __name__ == "__main__":
    main()
