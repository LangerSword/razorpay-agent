from __future__ import annotations

from fastapi.testclient import TestClient

from razorpay_agent.audit import AuditStore
from razorpay_agent.checkout.api import build_app
from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.payments import ScriptedPaymentProvider
from razorpay_agent.checkout.sessions import SessionRepository
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.merchant import MERCHANT_NAME


def _client():
    pipeline = OfferPipeline(
        None,
        RulePolicyGateConfig(fallback_bundle_item="sku-socks", fallback_bundle_price=499.0),
        AuditStore(":memory:"),
    )
    return TestClient(
        build_app(DEMO_CATALOG, SessionRepository(), pipeline, ScriptedPaymentProvider())
    )


class TestStorefront:
    def test_storefront_serves_html(self):
        resp = _client().get("/storefront")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert MERCHANT_NAME in resp.text

    def test_status_reports_fictional_merchant_and_badge(self):
        client = _client()
        base = client.get("/storefront/status")
        assert base.status_code == 200
        assert base.json()["merchant_name"] == MERCHANT_NAME
        assert base.json()["agent_active"] is False

        forced = client.get("/storefront/status?mode=agent")
        assert forced.json()["agent_active"] is True

    def test_agent_header_flips_presence(self):
        client = _client()
        # An AI buyer-agent hitting any endpoint with the agent header...
        client.get("/products", headers={"X-Razorpay-Agent": "buyer-agent"})
        # ...makes the storefront report an active agent session.
        assert client.get("/storefront/status").json()["agent_active"] is True
