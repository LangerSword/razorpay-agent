from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from datetime import datetime, timezone

import uvicorn

from razorpay_agent.buyer import BuyerAgent
from razorpay_agent.checkout.payments import RazorpayTestProvider, ScriptedPaymentProvider
from razorpay_agent.server import DEFAULT_DB_PATH, build_live_app

DEMO_PORT = 8612


def start_server():
    app, audit_store, is_live = build_live_app(DEFAULT_DB_PATH)
    config = uvicorn.Config(app, host="127.0.0.1", port=DEMO_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("demo server failed to start")
    return server, app.state.payment_provider, is_live


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def poll_link(provider, link_id: str, wait_seconds: int, interval: float = 3.0) -> dict | None:
    deadline = time.monotonic() + wait_seconds
    last_status = None
    while time.monotonic() < deadline:
        try:
            report = provider.payment_link_status(link_id)
        except Exception as exc:
            print(f"  [{stamp()}] status poll failed ({type(exc).__name__}: {exc}); retrying")
            time.sleep(interval)
            continue
        status = report["status"]
        if status != last_status:
            print(f"  [{stamp()}] payment link {link_id} status per Razorpay: {status}")
            last_status = status
        if status == "paid":
            return report
        time.sleep(interval)
    return None


async def run_purchase():
    agent = BuyerAgent(base_url=f"http://127.0.0.1:{DEMO_PORT}", payment_token="tok_demo")
    result = await agent.run_purchase("sku-hoodie")
    await agent.aclose()
    return agent, result


def main() -> None:
    parser = argparse.ArgumentParser(description="live created->paid capture demo")
    parser.add_argument("--wait", type=int, default=600, help="seconds to wait for manual payment")
    parser.add_argument("--skip-payment", action="store_true", help="stop after showing order status")
    args = parser.parse_args()

    server, provider, is_live = start_server()
    mode = "LIVE Razorpay test-mode" if is_live else "SCRIPTED provider"
    print(f"[capture-demo] server up on :{DEMO_PORT} — payments via {mode}\n")

    try:
        agent, result = asyncio.run(run_purchase())
        print("--- buyer-agent transcript ---")
        for line in agent.transcript:
            print(f"  buyer> {line}")

        order = (agent.last_session or {}).get("order")
        if not order:
            print("\n[capture-demo] purchase did not complete; nothing to track")
            return

        reference = order["id"]
        status_report = provider.order_status(reference)
        print(
            f"\n[{stamp()}] merchant order {reference} status per Razorpay API: "
            f"{status_report.status} "
            f"(amount {status_report.raw.get('amount')} paise, paid so far "
            f"{status_report.amount_paid_paise})"
        )

        if args.skip_payment:
            print("\n[capture-demo] --skip-payment set; stopping before hosted checkout")
            return

        amount_paise = status_report.raw.get("amount")
        if not isinstance(amount_paise, int):
            print("[capture-demo] could not read order amount from Razorpay; aborting link step")
            return

        try:
            link = provider.create_payment_link(
                amount_paise=amount_paise,
                currency="INR",
                description="razorpay-agent test-card capture",
                reference=reference,
            )
        except Exception as exc:
            print(
                f"\n[capture-demo] payment-link creation FAILED against Razorpay: "
                f"{type(exc).__name__}: {exc}"
            )
            print(">>> No hosted-checkout URL available; capture did NOT happen.")
            return
        print(f"\n[{stamp()}] payment link created: {link['id']} status={link['status']}")
        print(">>> OPEN THIS URL AND PAY WITH TEST CARD 4111 1111 1111 1111")
        print(f">>> {link['url']}")

        paid = poll_link(provider, link["id"], args.wait)

        final_order_status = provider.order_status(reference)
        print(
            f"\n[{stamp()}] final states — payment link {link['id']}: "
            f"{paid['status'] if paid else 'NOT PAID within wait window'}; "
            f"merchant order {reference}: {final_order_status.status}"
        )
        if paid:
            print(
                "\n>>> CAPTURE CONFIRMED: Razorpay reports the payment link PAID. "
                "Funds settled through Razorpay's hosted checkout."
            )
            print(
                f"    note: capture settled via the payment link's own Razorpay order; "
                f"merchant order {reference} remains '{final_order_status.status}' in the Orders API."
            )
        else:
            print(
                "\n>>> NOT CAPTURED: no payment completed inside the wait window. "
                "Nothing has been charged; reporting honestly rather than assuming success."
            )
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
