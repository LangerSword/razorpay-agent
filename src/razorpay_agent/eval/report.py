from razorpay_agent.eval.replay import HONESTY_NOTE
from razorpay_agent.eval.storage import EvalStore


def latest_report(eval_store: EvalStore) -> dict | None:
    summary = eval_store.latest_run_summary()
    if summary is None:
        return None

    bandit_steps = [s for s in summary["steps"] if s["policy"] == "bandit"]
    fallback_steps = [s for s in summary["steps"] if s["policy"] == "fallback"]

    buyer_accuracy = eval_store.latest_buyer_accuracy()
    merchant_reasoning = eval_store.latest_merchant_reasoning()

    report = {
        "run_id": summary["id"],
        "created_at": summary["created_at"],
        "seed": summary["seed"],
        "n_sessions": summary["n_sessions"],
        "metrics": {
            "uplift_over_baseline": {
                "bandit_mean_net_revenue": _mean([s["reward"] for s in bandit_steps]),
                "fallback_mean_net_revenue": _mean([s["reward"] for s in fallback_steps]),
                "delta_rupees_per_session": summary["uplift_over_baseline"],
            },
            "gate_compliance_rate": summary["gate_compliance_rate"],
            "cumulative_regret": summary["cumulative_regret"],
        },
        "honesty_note": HONESTY_NOTE,
    }

    if buyer_accuracy:
        report["buyer_accuracy"] = buyer_accuracy

    if merchant_reasoning:
        report["merchant_reasoning"] = merchant_reasoning

    return report


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
