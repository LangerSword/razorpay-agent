from __future__ import annotations

import json
import math
from pathlib import Path

from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.eval.replay import BUNDLE_ITEM_CATEGORIES, FALLBACK_ITEM
from razorpay_agent.eval.storage import EvalStore

DEFAULT_EPSILON = 0.05
DEFAULT_MIN_SAMPLES = 30
DEFAULT_MIN_EFFECTIVE_SAMPLE_SIZE = 30.0
AGREEMENT_CAVEAT_FLOOR = 0.70


def _policy_from_snapshot(snapshot_path: str | Path, alpha: float | None = None) -> LinUCBPolicy:
    state = json.loads(Path(snapshot_path).read_text())
    if alpha is not None:
        state["alpha"] = float(alpha)
    return LinUCBPolicy.from_state_dict(state)


def _context_from_row(row: dict) -> DecisionContext:
    return DecisionContext(
        session_id=row["session_id"],
        target_sku=row["target_sku"],
        item_category=row["item_category"],
        cart_value_inr=row["cart_value_rupees"],
        buyer_allowance_inr=row["buyer_allowance_rupees"],
    )


def _arm_id_for_action(action) -> str | None:
    if action is None:
        return None
    if action.action_type == "discount":
        return f"d{int(round(float(action.discount_percent)))}"
    for item in BUNDLE_ITEM_CATEGORIES:
        if action.bundle_item == item:
            return f"b_{item}"
    return None


def estimate_candidate_alpha(
    eval_store: EvalStore,
    snapshot_path: str | Path,
    alpha_candidate: float,
    epsilon: float = DEFAULT_EPSILON,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    min_ess: float = DEFAULT_MIN_EFFECTIVE_SAMPLE_SIZE,
) -> dict:
    rows = eval_store.logged_decisions()
    n = len(rows)
    base = {
        "alpha_candidate": alpha_candidate,
        "epsilon_kernel": epsilon,
        "n_logged_decisions": n,
        "min_samples_required": min_samples,
        "reward_metric": "aligned net revenue (§4.7 counterfactual convention)",
    }
    if n < min_samples:
        return {
            **base,
            "verdict": "insufficient_logged_decisions",
            "explanation": (
                f"only {n} resolved decisions logged; at least {min_samples} required "
                "before an off-policy estimate is worth reporting"
            ),
        }

    logging_policy = _policy_from_snapshot(snapshot_path)
    candidate_policy = _policy_from_snapshot(snapshot_path, alpha=alpha_candidate)

    arm_ids = list(logging_policy.arm_ids)
    k_arms = len(arm_ids)

    weights: list[float] = []
    rewards: list[float] = []
    agreements = 0

    for row in rows:
        context = _context_from_row(row)
        logged_arm = row["arm_id"]
        if logged_arm not in arm_ids:
            continue

        log_scores = logging_policy.scores(context)
        cand_scores = candidate_policy.scores(context)
        log_argmax = max(arm_ids, key=lambda a: log_scores[a])
        cand_argmax = max(arm_ids, key=lambda a: cand_scores[a])
        if log_argmax == logged_arm:
            agreements += 1

        pi_log = (1.0 - epsilon) if log_argmax == logged_arm else epsilon / k_arms
        pi_cand = (1.0 - epsilon) if cand_argmax == logged_arm else epsilon / k_arms

        weight = pi_cand / pi_log
        weights.append(weight)
        rewards.append(float(row["reward"]))

    used_n = len(weights)
    sum_w = sum(weights)
    if used_n == 0 or abs(sum_w) < 1e-12:
        return {
            **base,
            "verdict": "propensity_weights_too_degenerate",
            "explanation": "all importance weights vanished; no overlap between policies",
        }

    value = sum(w * r for w, r in zip(weights, rewards)) / sum_w
    ess = sum_w * sum_w / sum(w * w for w in weights)

    influence = [w * (r - value) for w, r in zip(weights, rewards)]
    stderr = math.sqrt(sum(term * term for term in influence)) / sum_w if used_n > 1 else float("inf")

    agreement_rate = agreements / used_n
    caveats: list[str] = []
    if ess < min_ess:
        return {
            **base,
            "verdict": "propensity_weights_too_degenerate",
            "effective_sample_size": ess,
            "explanation": (
                f"effective sample size {ess:.1f} below {min_ess:.0f}; the two policies "
                "agree too rarely over this window for a stable counterfactual estimate"
            ),
        }
    if agreement_rate < AGREEMENT_CAVEAT_FLOOR:
        caveats.append(
            f"snapshot argmax matches only {agreement_rate:.0%} of logged choices "
            "(live policy kept learning after the snapshot); treat magnitudes with care"
        )

    return {
        **base,
        "verdict": "stable_estimate",
        "estimated_net_revenue_per_decision": value,
        "standard_error": stderr,
        "confidence_interval_95": [
            value - 1.96 * stderr,
            value + 1.96 * stderr,
        ],
        "effective_sample_size": ess,
        "snapshot_agreement_rate": agreement_rate,
        "caveats": caveats,
        "method_note": (
            "Self-normalized inverse propensity scoring under a stated "
            f"uniform-exploration kernel (epsilon={epsilon}); both policies scored "
            "statically from the pretrained snapshot; candidate differs only in alpha."
        ),
    }


def compare_alpha_summary(eval_store: EvalStore, snapshot_path: str | Path, alpha: float) -> dict:
    return estimate_candidate_alpha(eval_store, snapshot_path, alpha)


FALLBACK_ITEM_REF = FALLBACK_ITEM
