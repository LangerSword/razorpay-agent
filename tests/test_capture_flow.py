import pytest

from razorpay_agent.checkout.api import build_app
from razorpay_agent.checkout.payments import (
    OrderStatusReport,
    RazorpayTestProvider,
    ScriptedPaymentProvider,
)


class _OrderNamespace:
    def __init__(self, outer):
        self._outer = outer

    def create(self, payload):
        order_id = f"plink_{len(self._outer.orders) + len(self._outer.links) + 1}"
        record = {"id": order_id, "status": "created"}
        record.update(payload)
        self._outer.orders[order_id] = record
        # Also store in links for mark_paid compatibility
        self._outer.links[order_id] = dict(record)
        return dict(record)

    def fetch(self, order_id):
        return dict(self._outer.orders[order_id])


class _PaymentLinkNamespace:
    def __init__(self, outer):
        self._outer = outer

    def create(self, payload):
        self._outer.created_link_payloads.append(payload)
        link_id = f"plink_{len(self._outer.links) + 1}"
        record = {"id": link_id, "short_url": f"https://rzp.io/i/{link_id}", "status": "created"}
        record.update(payload)
        self._outer.links[link_id] = record
        return dict(record)

    def fetch(self, link_id):
        return dict(self._outer.links[link_id])


class FakeRazorpayClient:
    def __init__(self):
        self.orders = {
            "order_ABCdef123": {
                "id": "order_ABCdef123",
                "status": "created",
                "amount": 237405,
                "amount_paid": 0,
            }
        }
        self.links = {}
        self.created_link_payloads = []
        self.order = _OrderNamespace(self)
        self.payment_link = _PaymentLinkNamespace(self)

    def mark_paid(self, link_id):
        self.links[link_id]["status"] = "paid"


@pytest.fixture
def provider():
    return RazorpayTestProvider("key_x", "secret_y", client=FakeRazorpayClient())


class TestOrderStatus:
    def test_reports_raw_razorpay_state(self, provider):
        report = provider.order_status("order_ABCdef123")
        assert isinstance(report, OrderStatusReport)
        assert report.status == "created"
        assert report.amount_paid_paise == 0
        assert report.raw["amount"] == 237405

    def test_reflects_paid_transition(self, provider):
        provider._client.orders["order_ABCdef123"]["status"] = "paid"
        provider._client.orders["order_ABCdef123"]["amount_paid"] = 237405
        report = provider.order_status("order_ABCdef123")
        assert report.status == "paid"
        assert report.amount_paid_paise == 237405


class TestPaymentLink:
    def test_create_returns_url_and_posts_sane_payload(self, provider):
        link = provider.create_payment_link(
            amount_paise=237405,
            currency="INR",
            description="capture demo",
            reference="order_ABCdef123",
        )
        assert link["id"].startswith("plink_")
        assert link["url"].startswith("https://checkout.razorpay.com/")
        sent = provider._client.orders[link["id"]]
        assert sent["amount"] == 237405
        assert sent["currency"] == "INR"
        assert sent["receipt"] == "order_ABCdef123"

    def test_status_tracks_created_then_paid(self, provider):
        link = provider.create_payment_link(100, "INR", "d", "r")
        assert provider.payment_link_status(link["id"])["status"] == "created"
        provider._client.mark_paid(link["id"])
        assert provider.payment_link_status(link["id"])["status"] == "paid"


class TestScriptedProviderParity:
    def test_scripted_order_status_is_always_created_and_unpaid(self):
        scripted = ScriptedPaymentProvider()
        report = scripted.order_status("test_ref_tok")
        assert report.status == "created"
        assert report.amount_paid_paise == 0

    def test_scripted_link_never_becomes_paid(self):
        scripted = ScriptedPaymentProvider()
        link = scripted.create_payment_link(500, "INR", "d", "r")
        for _ in range(3):
            assert scripted.payment_link_status(link["id"])["status"] == "created"

    def test_scripted_charge_still_works(self):
        result = ScriptedPaymentProvider().charge(100, "inr", "tok_ok")
        assert result.provider_reference == "test_ref_tok_ok"


class TestAppStateWiring:
    def test_provider_reachable_via_app_state(self):

        from razorpay_agent.audit import AuditStore
        from razorpay_agent.checkout.catalog import DEMO_CATALOG
        from razorpay_agent.checkout.offers import OfferPipeline
        from razorpay_agent.checkout.sessions import SessionRepository
        from razorpay_agent.gate import RulePolicyGateConfig

        scripted = ScriptedPaymentProvider()
        pipeline = OfferPipeline(None, RulePolicyGateConfig("sku-socks", 499.0), AuditStore(":memory:"))
        app = build_app(DEMO_CATALOG, SessionRepository(), pipeline, scripted)
        assert app.state.payment_provider is scripted
