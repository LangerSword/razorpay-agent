from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from razorpay_agent.eval.storage import EvalStore


def render_charts(eval_store_path: str, out_dir: str) -> list[str]:
    from pathlib import Path

    store = EvalStore(eval_store_path)
    summary = store.latest_run_summary()
    store.close()

    if summary is None:
        raise ValueError("no eval runs recorded yet")

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    steps = summary["steps"]
    sessions = sorted({int(s["step_index"]) // 2 for s in steps})
    bandit_by_session: dict[int, float] = {}
    fallback_by_session: dict[int, float] = {}
    for step in steps:
        session_index = int(step["step_index"]) // 2
        if step["policy"] == "bandit":
            bandit_by_session[session_index] = float(step["reward"])
        else:
            fallback_by_session[session_index] = float(step["reward"])

    diffs = [
        bandit_by_session.get(i, 0.0) - fallback_by_session.get(i, 0.0) for i in sessions
    ]
    cumulative_uplift = []
    running = 0.0
    for diff in diffs:
        running += diff
        cumulative_uplift.append(running)

    fig, axis = plt.subplots(figsize=(8, 4))
    axis.plot(sessions, cumulative_uplift, color="#1f77b4")
    axis.set_title("Cumulative net-revenue uplift vs rule-based fallback")
    axis.set_xlabel("synthetic session")
    axis.set_ylabel("uplift (rupees)")
    uplift_path = out / "uplift_over_time.png"
    fig.tight_layout()
    fig.savefig(uplift_path, dpi=150)
    plt.close(fig)
    written.append(str(uplift_path))

    regret_by_step = sorted(
        ((int(s["step_index"]), float(s["best_expected_reward"]) - float(s["chosen_expected_reward"]))
         for s in steps if s["policy"] == "bandit"),
    )
    cumulative_regret = []
    running = 0.0
    xs = []
    for order, (step_index, gap) in enumerate(regret_by_step):
        running += max(gap, 0.0)
        cumulative_regret.append(running)
        xs.append(order)

    fig, axis = plt.subplots(figsize=(8, 4))
    axis.plot(xs, cumulative_regret, color="#d62728")
    axis.set_title("Bandit cumulative pseudo-regret (should flatten as it learns)")
    axis.set_xlabel("decision index")
    axis.set_ylabel("regret (rupees)")
    regret_path = out / "regret_curve.png"
    fig.tight_layout()
    fig.savefig(regret_path, dpi=150)
    plt.close(fig)
    written.append(str(regret_path))

    return written
