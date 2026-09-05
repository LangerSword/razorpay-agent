from __future__ import annotations

from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy

CATEGORIES = ("apparel", "electronics")


def load_policy():
    return LinUCBPolicy.load("demo/pretrained_bandit.json")


def scores_for(policy, **overrides):
    ctx = DecisionContext(
        session_id="verify",
        target_sku="sku-hoodie",
        item_category="apparel",
        cart_value_inr=3999.0,
        buyer_allowance_inr=50000.0,
        **overrides,
    )
    # Both bundle upsells and discounts are valid clearance tactics for stale
    # stock, so all arms remain in the candidate set for stagnant sessions.
    return policy.scores(ctx)


def report(title, scores):
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    print(f"\n{title}")
    for arm_id, score in ordered:
        print(f"  {arm_id:>12} = {score:8.2f}")


def main() -> None:
    policy = load_policy()
    discount_arms = [a for a in policy.arm_ids if a.startswith("d")]

    stagnant = scores_for(policy, is_stagnant=True, days_in_stock=120)
    normal = scores_for(policy, is_stagnant=False, days_in_stock=0)

    report("STAGNANT (apparel, ₹3999, 120 days in stock):", stagnant)
    report("NORMAL   (apparel, ₹3999):", normal)

    stag_discounts = {a: stagnant[a] for a in discount_arms}
    norm_discounts = {a: normal[a] for a in discount_arms}

    stag_sorted = sorted(stag_discounts.values(), reverse=True)
    deepest = max(stag_discounts, key=stag_discounts.get)
    shallowest = min(stag_discounts, key=stag_discounts.get)
    monotonic = all(
        stag_discounts[f"d{a}"] >= stag_discounts[f"d{b}"]
        for a, b in zip((5, 10, 15, 20, 25, 35, 40), (10, 15, 20, 25, 35, 40, 99))
        if f"d{a}" in stag_discounts and f"d{b}" in stag_discounts
    ) if False else None

    print("\n--- CHECKS ---")
    print(f"stagnant deepest discount arm = {deepest} (score {stag_discounts[deepest]:.2f})")
    print(f"stagnant shallowest discount arm = {shallowest} (score {stag_discounts[shallowest]:.2f})")
    print(f"stagnant: d40 >= d5 ? {stag_discounts['d40'] >= stag_discounts['d5']}")
    print(f"stagnant: d40 is the TOP discount arm ? {deepest == 'd40'}")
    best_bundle = max(
        (a for a in stagnant if a.startswith("b")), key=lambda a: stagnant[a]
    )
    print(f"stagnant: bundle also a valid option ? {best_bundle} "
          f"(score {stagnant[best_bundle]:.2f})")

    norm_best = max(norm_discounts, key=norm_discounts.get)
    norm_worst = min(norm_discounts, key=norm_discounts.get)
    print(f"normal best discount arm = {norm_best} (score {norm_discounts[norm_best]:.2f})")
    print(f"normal worst discount arm = {norm_worst} (score {norm_discounts[norm_worst]:.2f})")
    print(f"normal: d5 is the BEST discount arm ? {norm_best == 'd5'}")
    print(f"normal: d40 is the WORST discount arm ? {norm_worst == 'd40'}")
    print(f"normal: d5 >> d40 (small beats deep) ? {norm_discounts['d5'] > norm_discounts['d40']}")


if __name__ == "__main__":
    main()
