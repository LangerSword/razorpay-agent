from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.eval.replay import pretrain_policy

DEFAULT_OUT = "demo/pretrained_bandit.json"
DEFAULT_SESSIONS = 5000


def main() -> None:
    parser = argparse.ArgumentParser(description="one-time bandit pretraining over synthetic episodes")
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument("--temperature", type=float, default=1.0,
                        help="softmax temperature for action selection "
                             "(0 = greedy argmax)")
    parser.add_argument("--out", type=str, default=DEFAULT_OUT)
    args = parser.parse_args()

    print(
        f"[pretrain] training LinUCB policy over {args.sessions} synthetic sessions "
        f"(seed {args.seed}, softmax temperature {args.temperature})..."
    )
    policy = pretrain_policy(
        n_sessions=args.sessions, seed=args.seed, temperature=args.temperature
    )
    policy.save(args.out)

    state = json.loads(Path(args.out).read_text())
    print(f"[pretrain] saved trained state to {args.out}")
    print(f"[pretrain] arms: {[arm['arm_id'] for arm in state['arms']]}")
    print(f"[pretrain] updates applied: {state['trained_sessions']}")

    print("\n[pretrain] sanity probe — what the warm policy proposes per context:")
    for category, cart in (("apparel", 2499.0), ("electronics", 4999.0), ("apparel", 1200.0)):
        context = DecisionContext(
            session_id=f"probe-{category}-{cart}",
            target_sku="sku-hoodie" if category == "apparel" else "sku-headphones",
            item_category=category,
            cart_value_inr=cart,
            buyer_allowance_inr=50000.0,
        )
        arm_id, action = policy.propose_with_arm(context)
        if action is None:
            print(f"  {category} ₹{cart}: abstains (no offer)")
        elif action.action_type == "discount":
            print(
                f"  {category} ₹{cart}: arm {arm_id} -> discount "
                f"{action.discount_percent:g}% (confidence {action.confidence:.2f})"
            )
        else:
            print(
                f"  {category} ₹{cart}: arm {arm_id} -> bundle {action.bundle_item} "
                f"@ ₹{action.bundle_price:g} (confidence {action.confidence:.2f})"
            )

    print(f"\n[pretrain] completed at {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
