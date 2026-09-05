"""razorpay-agent full demo — four-phase walkthrough with shop assistant.

Two modes:
1. Standalone (default): starts its own server, owns the pipeline, forces arms
2. Client (--client): connects to an existing server on :8613 (e.g. run_server.py),
   runs buyer purchases against it, and streams events through the shared audit store.

The client mode is what the live agent panel uses. Since it doesn't own the pipeline,
it can't force specific arms — it flows with the bandit's natural choices instead.

New in this version:
- Shop assistant greets buyer and recommends products by interest
- Payment link creation can fail (showcases graceful failure handling)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx
import uvicorn

from razorpay_agent.audit import AuditStore
from razorpay_agent.buyer import BuyerAgent
from razorpay_agent.checkout.catalog import DEMO_CATALOG, find_product
from razorpay_agent.checkout.payments import RazorpayTestProvider, ScriptedPaymentProvider
from razorpay_agent.eval.charts import render_charts
from razorpay_agent.eval.replay import run_offline_validation
from razorpay_agent.eval.storage import EvalStore
from razorpay_agent.reasoning.llm import resolve_provider
from razorpay_agent.server import DEFAULT_DB_PATH, build_live_app

DEMO_PORT = 8613
BANDIT_TEMPERATURE = 1.0
REASONER_TIMEOUT = 60


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def is_server_running() -> bool:
    try:
        resp = httpx.get(f"http://127.0.0.1:{DEMO_PORT}/products", timeout=2.0)
        return resp.status_code == 200
    except Exception:
        return False


def start_server():
    app, audit_store, is_live = build_live_app(
        DEFAULT_DB_PATH, temperature=BANDIT_TEMPERATURE, rng=random.Random()
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=DEMO_PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("demo server failed to start")
    return server, app.state.payment_provider, is_live, app.state.pipeline


def new_buyer(token: str, llm=None) -> BuyerAgent:
    return BuyerAgent(
        base_url=f"http://127.0.0.1:{DEMO_PORT}",
        payment_token=token,
        llm=llm,
    )


async def purchase(item_id: str, quantity: int, token: str, llm=None):
    agent = new_buyer(token, llm=llm)
    result = await agent.run_purchase(item_id, quantity)
    session_id = (agent.last_session or {}).get("id")

    if session_id:
        await _poll_merchant_reasoning(agent, session_id)

    await agent.aclose()
    return agent, result


async def _poll_merchant_reasoning(agent: BuyerAgent, session_id: str) -> None:
    deadline = time.monotonic() + REASONER_TIMEOUT

    while time.monotonic() < deadline:
        try:
            resp = httpx.get(
                f"http://127.0.0.1:{DEMO_PORT}/checkout_sessions/{session_id}",
                timeout=2.0,
            )
            if resp.status_code == 200:
                reasoning = resp.json().get("reasoning")
                if reasoning and not _still_running(reasoning):
                    _append_merchant_reasoning_to_transcript(agent, reasoning)
                    break
        except Exception:
            pass
        await asyncio.sleep(2.0)


def _still_running(reasoning: dict) -> bool:
    if reasoning.get("fallback"):
        return False
    ft = reasoning.get("final_text", "")
    if len(ft) > 50:
        return False
    return True


def _append_merchant_reasoning_to_transcript(agent: BuyerAgent, reasoning: dict) -> None:
    ft = reasoning.get("final_text", "").strip()
    fallback = reasoning.get("fallback", False)

    if fallback:
        agent.transcript.append("  merchant LLM: (unavailable — using bandit+gate only)")
        return

    if not ft:
        agent.transcript.append("  merchant LLM: (completed, no final assessment)")
        return

    if ft.startswith("<<tool:") or ft.startswith("<tool_call>"):
        agent.transcript.append("  merchant LLM: (tool-only response)")
        return

    low = ft.lower()
    if "verdict: approve" in low:
        verdict = "APPROVE"
    elif "verdict: reject" in low:
        verdict = "REJECT"
    elif "verdict: review" in low:
        verdict = "REVIEW"
    elif "approve" in low:
        verdict = "APPROVE"
    elif "reject" in low:
        verdict = "REJECT"
    else:
        verdict = "REVIEW"

    assessment_lines = []
    for line in ft.split("\n"):
        if line.lower().startswith("verdict:"):
            break
        stripped = line.strip()
        if stripped.startswith("-") or stripped.startswith("**"):
            assessment_lines.append(stripped)

    assessment = " ".join(assessment_lines)
    if len(assessment) > 200:
        assessment = assessment[:197].rsplit(" ", 1)[0] + "..."

    if assessment:
        agent.transcript.append(f"  merchant LLM: {assessment}")
    agent.transcript.append(f"  merchant verdict: {verdict}")


class Runner:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.log_lines: list[str] = []

    def say(self, text: str = "") -> None:
        print(text)
        self.log_lines.append(text)

    def section(self, title: str) -> None:
        self.say("")
        self.say(f"=== {title} === ")


async def run_phases(runner: Runner, pipeline, args, llm):
    """Run demo phases. Pipeline may be None in client mode."""
    results: dict[str, dict] = {}

    # Phase A: normal accept flow
    runner.section("PHASE A — normal accept flow (warm bandit, softmax)")
    runner.say("  bandit arm: chosen by softmax (temperature 1.0)")
    agent_a, result_a = await purchase("sku-hoodie", 1, "tok_ok", llm=llm)
    for line in agent_a.transcript:
        runner.say(f"  buyer> {line}")
    results["phase_a"] = {
        "status": result_a.final_status,
        "order": result_a.order,
        "session": agent_a.last_session,
        "transcript": list(agent_a.transcript),
    }

    # Phase B: gate-cap moment (only if we own the pipeline)
    if pipeline is not None:
        runner.section("PHASE B — the gate-cap moment (bandit preference meets hard limit)")
        runner.say("  forced arm: d40 (40% discount) → gate should cap to ~12% (300 rupee cap)")
        pipeline.force_next_arm("d40")
        agent_b, result_b = await purchase("sku-hoodie", 1, "tok_ok", llm=llm)
    else:
        runner.section("PHASE B — second purchase (bandit natural choice)")
        runner.say("  (client mode: bandit chooses naturally)")
        agent_b, result_b = await purchase("sku-hoodie", 1, "tok_ok", llm=llm)
    for line in agent_b.transcript:
        runner.say(f"  buyer> {line}")
    results["phase_b"] = {
        "status": result_b.final_status,
        "order": result_b.order,
        "session": agent_b.last_session,
        "transcript": list(agent_b.transcript),
    }

    # Phase C: graceful failure
    runner.section("PHASE C — graceful failure (declined credential, never retried)")
    runner.say("  NOTE: LLM verdict evaluates the OFFER, not the payment outcome")
    agent_c, result_c = await purchase("sku-hoodie", 1, "tok_declined", llm=llm)
    for line in agent_c.transcript:
        runner.say(f"  buyer> {line}")
    if result_c.final_status != "completed":
        runner.say("  gate>   payment was declined by the provider → session NOT completed")
        runner.say("  gate>   no retry attempted (architecture: one decision per session)")
    results["phase_c"] = {
        "status": result_c.final_status,
        "session": agent_c.last_session,
        "transcript": list(agent_c.transcript),
    }

    # Phase C2: stagnant inventory clearance
    runner.section("PHASE C2 — stagnant inventory clearance (structural catalog fact)")
    runner.say("  catalog fact: sku-oldstock flagged stagnant=true, 120 days in stock")
    agent_s, result_s = await purchase("sku-oldstock", 1, "tok_ok", llm=llm)
    for line in agent_s.transcript:
        runner.say(f"  buyer> {line}")
    results["stagnant"] = {
        "status": result_s.final_status,
        "order": result_s.order,
        "session": agent_s.last_session,
        "transcript": list(agent_s.transcript),
    }

    # Phase D: live Razorpay settlement chain
    runner.section("PHASE D — live Razorpay settlement chain")
    agent_d, result_d = await purchase("sku-hoodie", 1, "tok_demo", llm=llm)
    for line in agent_d.transcript:
        runner.say(f"  buyer> {line}")
    results["phase_d"] = {
        "status": result_d.final_status,
        "order": result_d.order,
        "session": agent_d.last_session,
        "transcript": list(agent_d.transcript),
    }

    return results


def write_outputs(runner: Runner, out_dir: Path, results: dict, db_path: str) -> None:
    (out_dir / "demo_transcript.txt").write_text("\n".join(runner.log_lines) + "\n")

    store = AuditStore(db_path)
    entries = [entry.to_dict() for entry in store.recent(limit=10)]
    store.close()
    (out_dir / "audit_tail.json").write_text(json.dumps(entries, indent=2))

    summary = [
        "# razorpay-agent full demo outputs",
        f"generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "| phase | outcome | evidence file |",
        "|---|---|---|",
    ]
    label = {
        "phase_a": "A accept flow",
        "phase_b": "B gate-cap moment",
        "phase_c": "C graceful failure",
        "stagnant": "C2 stagnant clearance",
        "phase_d": "D live settlement",
    }
    for key in ("phase_a", "phase_b", "phase_c", "stagnant", "phase_d"):
        data = results.get(key, {})
        order = (data.get("order") or {}).get("id") or "-"
        summary.append(
            f"| {label[key]} | {data.get('status', '-')} (order {order}) | demo_transcript.txt / audit_tail.json |"
        )
    summary += [
        "",
        "charts: uplift_over_time.png, regret_curve.png (offline validation, controlled conditions)",
    ]
    (out_dir / "summary.md").write_text("\n".join(summary) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="one-command end-to-end demo")
    parser.add_argument("--wait", type=int, default=600, help="seconds to wait for manual payment")
    parser.add_argument("--skip-payment", action="store_true", help="stop before hosted checkout")
    parser.add_argument("--client", action="store_true", help="connect to existing server on :8613")
    parser.add_argument(
        "--buyer-pays",
        action="store_true",
        help="simulate buyer paying the payment link (demo-only)",
    )
    args = parser.parse_args()

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("demo/out") / run_ts
    out_dir.mkdir(parents=True, exist_ok=True)

    runner = Runner(out_dir)
    runner.say(f"[full-demo] outputs will be written to {out_dir}")

    # Offline validation
    eval_store = EvalStore(DEFAULT_DB_PATH)
    validation = run_offline_validation(eval_store, seed=7, n_sessions=400)
    render_charts(DEFAULT_DB_PATH, str(out_dir))
    runner.say(
        f"[full-demo] offline validation refreshed: "
        f"bandit mean {validation['bandit_mean_net_revenue']:.2f} rps/session, "
        f"fallback mean {validation['fallback_mean_net_revenue']:.2f} rps/session, "
        f"uplift {validation['uplift_over_baseline']:.2f}, "
        f"compliance {validation['gate_compliance_rate']*100:.1f}% (controlled conditions, not revenue forecast)"
    )

    # Determine mode
    server = None
    pipeline = None
    is_live = True
    
    if args.client or is_server_running():
        runner.say(f"[full-demo] connecting to existing server on :{DEMO_PORT} (client mode)")
    else:
        runner.say(f"[full-demo] starting server on :{DEMO_PORT}")
        server, provider, is_live, pipeline = start_server()
        runner.say(f"[full-demo] server up — payments via {'LIVE Razorpay test-mode' if is_live else 'SCRIPTED (no API key)'}")

    # Resolve LLM
    llm = resolve_provider()
    runner.say(f"[full-demo] buyer reasoner LLM: {llm.name}/{llm.model}")

    results = asyncio.run(run_phases(runner, pipeline, args, llm))

    write_outputs(runner, out_dir, results, DEFAULT_DB_PATH)
    if server:
        server.should_exit = True


if __name__ == "__main__":
    main()
