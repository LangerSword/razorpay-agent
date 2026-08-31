from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from razorpay_agent.audit import AuditStore
from razorpay_agent.checkout.api import build_app
from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.inventory import InventoryStore
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.payments import ScriptedPaymentProvider
from razorpay_agent.checkout.sessions import SessionRepository
from razorpay_agent.gate.gate import RulePolicyGateConfig


def store(stock=None):
    return InventoryStore(stock or {"a": 2, "b": 5})


class TestInventoryReservation:
    def test_available_starts_at_total(self):
        inv = store()
        assert inv.available("a") == 2
        assert inv.available("b") == 5

    def test_reserve_reduces_available(self):
        inv = store()
        assert inv.reserve_for_session("s1", [("a", 1)]) is True
        assert inv.available("a") == 1
        assert inv.reserved("a") == 1

    def test_release_returns_stock(self):
        inv = store()
        inv.reserve_for_session("s1", [("a", 2)])
        inv.release_session("s1")
        assert inv.available("a") == 2
        assert inv.reserved("a") == 0
        assert inv.has_session("s1") is False

    def test_commit_permanently_removes(self):
        inv = store()
        inv.reserve_for_session("s1", [("a", 1)])
        inv.commit_session("s1")
        assert inv.total("a") == 1
        assert inv.available("a") == 1
        assert inv.reserved("a") == 0

    def test_anti_double_sell_blocks_oversell(self):
        inv = store({"a": 2})
        assert inv.reserve_for_session("s1", [("a", 1)]) is True
        assert inv.reserve_for_session("s2", [("a", 1)]) is True
        # Only 2 units exist; a third reservation must fail and reserve nothing.
        assert inv.reserve_for_session("s3", [("a", 1)]) is False
        assert inv.available("a") == 0

    def test_partial_reservation_is_all_or_nothing(self):
        inv = store({"a": 1, "b": 1})
        # Requesting 2 of "a" cannot be satisfied; nothing should be reserved.
        assert inv.reserve_for_session("s1", [("a", 2)]) is False
        assert inv.reserved("a") == 0
        assert inv.reserved("b") == 0

    def test_rereserve_releases_prior(self):
        inv = store({"a": 3})
        inv.reserve_for_session("s1", [("a", 2)])
        inv.reserve_for_session("s1", [("a", 1)])  # should release 2, reserve 1
        assert inv.reserved("a") == 1
        assert inv.available("a") == 2

    def test_concurrent_reservations_do_not_oversell(self):
        inv = InventoryStore({"a": 50})
        sessions = [f"s{i}" for i in range(100)]

        def worker(sid):
            inv.reserve_for_session(sid, [("a", 1)])

        threads = [threading.Thread(target=worker, args=(sid,)) for sid in sessions]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only 50 units available; at most 50 sessions succeed.
        succeeded = sum(1 for sid in sessions if inv.has_session(sid))
        assert succeeded == 50
        assert inv.available("a") == 0

    def test_from_catalog_seeds_stock(self):
        class P:
            def __init__(self, id):
                self.id = id

        inv = InventoryStore.from_catalog([P("x"), P("y")], default_stock=7)
        assert inv.total("x") == 7
        assert inv.total("y") == 7


def _future(minutes: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _api_app(inventory: InventoryStore) -> TestClient:
    pipeline = OfferPipeline(
        None,
        RulePolicyGateConfig(fallback_bundle_item="sku-socks", fallback_bundle_price=499.0),
        AuditStore(":memory:"),
    )
    app = build_app(
        DEMO_CATALOG,
        SessionRepository(),
        pipeline,
        ScriptedPaymentProvider(),
        inventory=inventory,
    )
    return TestClient(app)


def _checkout_body(product_id: str = "sku-hoodie") -> dict:
    return {
        "items": [{"id": product_id, "quantity": 1}],
        "allowance": {
            "max_amount": 10_000_000,
            "currency": "inr",
            "expires_at": _future(),
        },
    }


class TestApiReservation:
    def test_anti_double_sell_blocks_second_session(self):
        inv = InventoryStore({"sku-hoodie": 1})
        http = _api_app(inv)

        first = http.post("/checkout_sessions", json=_checkout_body())
        assert first.status_code == 201
        assert first.json()["status"] == "ready_for_payment"

        # The single unit is now reserved; a second session must be refused.
        second = http.post("/checkout_sessions", json=_checkout_body())
        assert second.status_code == 201
        body = second.json()
        assert body["status"] != "ready_for_payment"
        assert any(m.get("code") == "out_of_stock" for m in body.get("messages", []))

    def test_completion_releases_capacity_for_next_buyer(self):
        inv = InventoryStore({"sku-hoodie": 1})
        http = _api_app(inv)

        first = http.post("/checkout_sessions", json=_checkout_body()).json()
        session_id = first["id"]

        # Completing the first session commits the sale, freeing nothing new, but a
        # cancel/release path must make stock available again.
        inv.release_session(session_id)
        freed = http.post("/checkout_sessions", json=_checkout_body())
        assert freed.json()["status"] == "ready_for_payment"
