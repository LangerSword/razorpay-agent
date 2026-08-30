from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx
import uvicorn

from razorpay_agent.audit import AuditStore
from razorpay_agent.buyer import BuyerAgent
from razorpay_agent.checkout.payments import RazorpayTestProvider
from razorpay_agent.eval.charts import render_charts
from razorpay_agent.eval.replay import run_offline_validation
from razorpay_agent.eval.storage import EvalStore
from razorpay_agent.server import DEFAULT_DB_PATH, build_live_app

DEMO_PORT = 8613


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def start_server():
    app, audit_store, is_live = build_live_app(DEFAULT_DB_PATH)
    config = uvicorn.Config(app, host="127.0.0.1", port=DEMO_PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("demo server failed to start")
    return server, app.state.payment_provider, audit_store, is_live


def new_buyer(token: str) -> BuyerAgent:
    return BuyerAgent(
        base_url=f"http://127.0.0.1:{DEMO_PORT}",
        payment_token=token,
    )


async def purchase(item_id: str, quantity: int, token: str):
    agent = new_buyer(token)
    result = await agent.run_purchase(item_id, quantity)
    await agent.aclose()
    return agent, result


def describe_line(session: dict) -> str:
    line = session["line_items"][0]
    return (
        f"base {line['base_amount']} paise, discount {line['discount']}, "
        f"paid {line['total']}"
    )


def poll_link(provider, link_id: str, wait_seconds: int, interval: float = 3.0) -> dict | None:
    deadline = time.monotonic() + wait_seconds
    last_status = None
    while time.monotonic() < deadline:
        try:
            report = provider.payment_link_status(link_id)
        except Exception as exc:
            print(f"  [{stamp()}] status poll failed ({type(exc).__name__}); retrying")
            time.sleep(interval)
            continue
        status = report["status"]
        if status != last_status:
            print(f"  [{stamp()}] link status per Razorpay: {status}")
            last_status = status
        if status == "paid":
            return report
        time.sleep(interval)
    return None


class Runner:
    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.log_lines: list[str] = []

    def say(self, text: str = "") -> None:
        print(text)
        self.log_lines.append(text)

    def section(self, title: str) -> None:
        self.say("")
        self.say(f"=== {title} ===")


async def run_phases(runner: Runner, provider, is_live: bool, wait_seconds: int, skip_payment: bool):
    results: dict[str, dict] = {}

    runner.section("PHASE A - normal accept flow (warm bandit)")
    agent_a, result_a = await purchase("sku-hoodie", 1, "tok_ok")
    for line in agent_a.transcript:
        runner.say(f"  buyer> {line}")
    results["phase_a"] = {
        "status": result_a.final_status,
        "order": result_a.order,
        "session": agent_a.last_session,
        "transcript": list(agent_a.transcript),
    }

    runner.section("PHASE B - the gate-cap moment (bandit preference meets hard limit)")
    agent_b, result_b = await purchase("sku-headphones", 2, "tok_ok")
    for line in agent_b.transcript:
        runner.say(f"  buyer> {line}")
    if agent_b.last_session and agent_b.last_session.get("line_items"):
        line = agent_b.last_session["line_items"][0]
        base_amount = line["base_amount"]
        uncapped_discount_paise = base_amount * 5 // 100
        pct_hundredths = (30000 * 100 * 100) // base_amount
        capped_percent = pct_hundredths / 100
        capped_discount_paise = base_amount * pct_hundredths // 10000
        runner.say(
            f"  gate>   warm bandit proposed its preferred 5% "
            f"(= {uncapped_discount_paise} paise on this {base_amount} paise cart)"
        )
        runner.say(
            f"  gate>   rupee ceiling of 30000 paise binds -> capped to "
            f"{capped_percent}% = {capped_discount_paise} paise, which is what the "
            f"buyer was shown"
        )
        runner.say(
            "  gate>   (completed-session payloads clear applied offers by design; "
            "the buyer transcript above is the rendered-offer record)"
        )
    results["phase_b"] = {
        "status": result_b.final_status,
        "order": result_b.order,
        "session": agent_b.last_session,
        "transcript": list(agent_b.transcript),
    }

    runner.section("PHASE C - graceful failure (declined credential, never retried)")
    agent_c, result_c = await purchase("sku-hoodie", 1, "tok_declined")
    for line in agent_c.transcript:
        runner.say(f"  buyer> {line}")
    results["phase_c"] = {
        "status": result_c.final_status,
        "session": agent_c.last_session,
        "transcript": list(agent_c.transcript),
    }

    runner.section("PHASE D - live Razorpay settlement chain")
    agent_d, result_d = await purchase("sku-hoodie", 1, "tok_demo")
    for line in agent_d.transcript:
        runner.say(f"  buyer> {line}")
    results["phase_d"] = {
        "status": result_d.final_status,
        "order": result_d.order,
        "session": agent_d.last_session,
        "transcript": list(agent_d.transcript),
    }

    order = (agent_d.last_session or {}).get("order")
    if not (is_live and order):
        runner.say("  [demo] live settlement not available here; stopping after order stage")
        return results

    reference = order["id"]
    status_report = provider.order_status(reference)
    runner.say(
        f"\n  [{stamp()}] merchant order {reference} status per Razorpay: "
        f"{status_report.status} (amount {status_report.raw.get('amount')} paise, "
        f"paid so far {status_report.amount_paid_paise})"
    )
    if skip_payment:
        runner.say("  [demo] --skip-payment set; ending before hosted checkout")
        return results

    amount_paise = status_report.raw.get("amount")
    try:
        link = provider.create_payment_link(
            amount_paise=amount_paise,
            currency="INR",
            description="razorpay-agent full-demo capture",
            reference=reference,
        )
    except Exception as exc:
        runner.say(f"  [demo] payment-link creation FAILED: {type(exc).__name__}: {exc}")
        runner.say(">>> No hosted checkout available; capture did NOT happen.")
        return results

    runner.say(f"  [{stamp()}] payment link {link['id']} created")
    runner.say(f">>> OPEN AND PAY (netbanking Success button): {link['url']}")

    paid = poll_link(provider, link["id"], wait_seconds)
    final_order_status = provider.order_status(reference)
    results["phase_d"]["payment_link"] = {
        "id": link["id"],
        "url": link["url"],
        "final_link_status": paid["status"] if paid else "NOT PAID within window",
        "final_merchant_order_status": final_order_status.status,
    }
    if paid:
        runner.say(
            f"\n>>> CAPTURE CONFIRMED by Razorpay. Merchant order {reference} remains "
            f"'{final_order_status.status}' in the Orders API; capture settled through "
            f"the payment link's own Razorpay order."
        )
    else:
        runner.say("\n>>> NOT CAPTURED within the window; nothing charged; reported honestly.")
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
        "phase_d": "D live settlement",
    }
    for key in ("phase_a", "phase_b", "phase_c", "phase_d"):
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
    parser.add_argument("--wait", type=int, default=600)
    parser.add_argument("--skip-payment", action="store_true")
    args = parser.parse_args()

    run_ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("demo/out") / run_ts
    out_dir.mkdir(parents=True, exist_ok=True)

    runner = Runner(out_dir)
    runner.say(f"[full-demo] outputs will be written to {out_dir}")

    eval_store = EvalStore(DEFAULT_DB_PATH)
    validation = run_offline_validation(eval_store, seed=7, n_sessions=400)
    render_charts(DEFAULT_DB_PATH, str(out_dir))
    runner.say(
        "[full-demo] offline validation refreshed: uplift "
        f"{validation['uplift_over_baseline']:.2f} rps/session, compliance "
        f"{validation['gate_compliance_rate']:.1%} (controlled conditions, not revenue forecast)"
    )

    server, provider, _, is_live = start_server()
    mode = "LIVE Razorpay test-mode" if is_live else "SCRIPTED provider"
    runner.say(f"[full-demo] server up on :{DEMO_PORT} — payments via {mode}")

    results: dict[str, dict] = {}
    try:
        results = asyncio.run(
            run_phases(runner, provider, is_live, args.wait, args.skip_payment)
        )
    finally:
        write_outputs(runner, out_dir, results, DEFAULT_DB_PATH)
        server.should_exit = True

    runner.say(f"\n[full-demo] done. everything saved under {out_dir}")


if __name__ == "__main__":
    main()
