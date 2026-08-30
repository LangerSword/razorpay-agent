from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx2 as httpx
import uvicorn

from razorpay_agent.buyer import BuyerAgent
from razorpay_agent.server import DEFAULT_DB_PATH, build_live_app
from razorpay_agent.audit import AuditStore

DEMO_PORT = 8611


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
    return server, audit_store, is_live


def print_audit_tail(db_path: str) -> None:
    store = AuditStore(db_path)
    entries = store.recent(limit=5)
    store.close()
    print("\n--- audit trail (latest 5) ---")
    for entry in entries:
        outcome = entry.outcome.status.upper()
        action = entry.proposed_action.action_type
        detail = entry.outcome.detail or entry.gate_decision.reason
        print(f"  [{entry.timestamp:%H:%M:%S}] {outcome:<9} {action:<13} {detail[:90]}")


async def run_buyer() -> BuyerAgent:
    agent = BuyerAgent(
        base_url=f"http://127.0.0.1:{DEMO_PORT}",
        payment_token="tok_demo",
    )
    result = await agent.run_purchase("sku-hoodie")
    return agent


def main() -> None:
    server, _, is_live = start_server()
    mode = "LIVE Razorpay test-mode" if is_live else "SCRIPTED provider"
    print(f"[razorpay-agent demo] server up on :{DEMO_PORT} — payments via {mode}\n")

    try:
        agent = asyncio.run(run_buyer())

        print("--- buyer-agent transcript ---")
        for line in agent.transcript:
            print(f"  buyer> {line}")

        final_session = agent.last_session
        print("\n--- final checkout session ---")
        print(json.dumps(final_session, indent=2)[:2000])

        order = final_session.get("order")
        if order:
            print(f"\n>>> RESULT: order id {order['id']}")

        print_audit_tail(DEFAULT_DB_PATH)

        report = httpx.get(f"http://127.0.0.1:{DEMO_PORT}/eval/report").json()
        print("\n--- /eval/report ---")
        print(json.dumps(report.get("metrics", report), indent=2))
        if "honesty_note" in report and report["honesty_note"]:
            print(f"note: {report['honesty_note']}")
    finally:
        server.should_exit = True


if __name__ == "__main__":
    main()
