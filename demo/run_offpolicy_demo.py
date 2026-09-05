from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from razorpay_agent.audit import AuditStore
from razorpay_agent.core.currency import INR
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.sessions import CheckoutSessionState, SessionRepository
from razorpay_agent.eval.offpolicy import estimate_candidate_alpha
from razorpay_agent.eval.replay import FALLBACK_ITEM
from razorpay_agent.eval.storage import EvalStore
from razorpay_agent.eval.synthetic import SimSession, SimulatedBuyerModel
from razorpay_agent.gate import RulePolicyGateConfig
from razorpay_agent.server import (
    PRETRAINED_BANDIT_PATH,
    build_policy,
    DEFAULT_DB_PATH,
)

DEMO_DB = "demo/offpolicy_demo.sqlite3"
SESSION_COUNT = 260


def main() -> None:
    parser = argparse.ArgumentParser(description="off-policy counterfactual evaluation demo")
    parser.add_argument("--alpha", type=float, default=0.25)
    parser.add_argument("--sessions", type=int, default=SESSION_COUNT)
    args = parser.parse_args()

    Path(DEMO_DB).unlink(missing_ok=True)
    rng = random.Random(7)
    buyer_model = SimulatedBuyerModel(random.Random(8))

    policy, is_warm = build_policy(PRETRAINED_BANDIT_PATH)
    eval_store = EvalStore(DEMO_DB)
    repo = SessionRepository()
    audit_store = AuditStore(":memory:")
    pipeline = OfferPipeline(
        policy,
        RulePolicyGateConfig(FALLBACK_ITEM, 499.0),
        audit_store,
        decision_log=eval_store,
    )

    print(f"[offpolicy-demo] logging {args.sessions} real decisions through the live pipeline...")
    accepted = declined = rejected = 0
    for index in range(args.sessions):
        category = rng.choice(("apparel", "home"))
        cart_rupees = rng.uniform(800.0, 5000.0)
        state = CheckoutSessionState(
            id=f"ope-{index}",
            status="ready_for_payment",
            currency=INR,
            items=[{"product_id": "sku-hoodie", "quantity": 1}],
            allowance_max_paise=10_000_000,
            allowance_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        repo.save(state)
        offer = pipeline.propose_for_session(
            state, int(cart_rupees * 100), "sku-hoodie", category
        )
        if not offer.gate_decision.allowed:
            rejected += 1
            continue

        final_action = offer.gate_decision.final_action
        sim_session = SimSession(
            index=index,
            category=category,
            cart_value_rupees=cart_rupees,
            allowance_rupees=12000.0,
            base_completion_prob=0.85,
        )
        if final_action.action_type == "discount":
            response = buyer_model.respond_to_discount(
                sim_session, float(final_action.discount_percent)
            )
        else:
            from razorpay_agent.eval.replay import BUNDLE_ITEM_CATEGORIES

            match = (
                final_action.bundle_item in BUNDLE_ITEM_CATEGORIES
                and BUNDLE_ITEM_CATEGORIES[final_action.bundle_item][0] == category
            )
            response = buyer_model.respond_to_bundle(
                sim_session, float(final_action.bundle_price), match
            )

        if response == "completed_accepted":
            paid_total = (
                int(int(cart_rupees * 100) * (1 - float(final_action.discount_percent) / 100))
                if final_action.action_type == "discount"
                else int(cart_rupees * 100) + int(float(final_action.bundle_price) * 100)
            )
            pipeline.resolve_accepted(state, paid_total, int(cart_rupees * 100))
            accepted += 1
        elif response == "abandoned":
            pipeline.resolve_declined(state, "buyer abandoned after offer")
            declined += 1
        else:
            pipeline.resolve_declined(state, "declined offer, bought anyway")
            declined += 1

    print(f"  resolved: {accepted} completed-accepted, {declined} declined/abandoned, "
          f"{rejected} gate-rejected")
    print(f"  usable logged decisions: {eval_store.logged_decision_count()}")

    result = estimate_candidate_alpha(eval_store, PRETRAINED_BANDIT_PATH, args.alpha)

    print(f"\n=== off-policy estimate: what an alpha={args.alpha} policy would have earned ===")
    if result["verdict"] == "stable_estimate":
        est = result["estimated_net_revenue_per_decision"]
        lo, hi = result["confidence_interval_95"]
        print(f"  estimated net revenue per decision: {est:.2f} rupees "
              f"(95% CI {lo:.2f} .. {hi:.2f})")
        print(f"  effective sample size: {result['effective_sample_size']:.0f} "
              f"of {result['n_logged_decisions']} logged decisions")
        print(f"  snapshot agreement rate: {result['snapshot_agreement_rate']:.0%}")
        for caveat in result.get("caveats", []):
            print(f"  caveat: {caveat}")
    else:
        print(f"  VERDICT: {result['verdict']}")
        print(f"  {result['explanation']}")
    print(f"  reward metric: {result['reward_metric']}")
    print(f"  method: {result.get('method_note', 'n/a')}")
    print(f"\n  same numbers available at GET /eval/offpolicy?alpha={args.alpha} "
          f"and inside /eval/report on the live server ({DEFAULT_DB_PATH} is the "
          f"production log; this demo used {DEMO_DB})")


if __name__ == "__main__":
    main()