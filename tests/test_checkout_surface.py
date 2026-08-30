from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from razorpay_agent.audit import AuditStore
from razorpay_agent.checkout import (
    DEMO_CATALOG,
    OfferPipeline,
    PaymentOutcome,
    ScriptedPaymentProvider,
    SessionRepository,
    build_app,
)
from razorpay_agent.decision import (
    BundleArm,
    ContextEncoder,
    DecisionContext,
    DiscountArm,
    LinUCBPolicy,
)
from razorpay_agent.gate import RulePolicyGateConfig

CATEGORIES = tuple(sorted({p.category for p in DEMO_CATALOG}))

ARMS = (
    DiscountArm("d5", 5.0),
    DiscountArm("d10", 10.0),
    DiscountArm("d20", 20.0),
    BundleArm("b_charger", "sku-charger", 1499.0),
)

GATE_CONFIG = RulePolicyGateConfig(
    fallback_bundle_item="sku-socks",
    fallback_bundle_price=499.0,
)


def future(minutes=30):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def make_app(policy=None, provider=None):
    audit = AuditStore(":memory:")
    repo = SessionRepository()
    pipeline = OfferPipeline(policy, GATE_CONFIG, audit)
    app = build_app(DEMO_CATALOG, repo, pipeline, provider or ScriptedPaymentProvider())
    return app, audit


def client(policy=None, provider=None):
    app, audit = make_app(policy=policy, provider=provider)
    return TestClient(app), audit


def fresh_policy():
    return LinUCBPolicy(ARMS, ContextEncoder(CATEGORIES), alpha=0.5)


def create_body(items=None, **allowance_overrides):
    allowance = {"max_amount": 10_000_000, "currency": "inr", "expires_at": future()}
    allowance.update(allowance_overrides)
    return {
        "items": items or [{"id": "sku-hoodie", "quantity": 1}],
        "allowance": allowance,
    }


class TestProductFeed:
    def test_feed_lists_catalog_with_integer_amounts(self):
        http, _ = client()
        response = http.get("/products")
        assert response.status_code == 200
        items = response.json()["items"]
        assert {item["id"] for item in items} == {p.id for p in DEMO_CATALOG}
        assert all(isinstance(item["unit_amount"], int) for item in items)


class TestCreateSession:
    def test_create_returns_acp_session_shape(self):
        http, _ = client()
        response = http.post("/checkout_sessions", json=create_body())
        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "ready_for_payment"
        assert payload["currency"] == "inr"
        assert payload["payment_provider"] == {
            "provider": "razorpay",
            "supported_payment_methods": ["card"],
        }
        line = payload["line_items"][0]
        assert line["base_amount"] == 249900 and isinstance(line["base_amount"], int)

    def test_create_applies_gate_approved_discount(self):
        policy = fresh_policy()
        context = DecisionContext(
            session_id="x",
            target_sku="sku-hoodie",
            item_category="apparel",
            cart_value_inr=2499.0,
            buyer_allowance_inr=100000.0,
        )
        for _ in range(6):
            policy.update("d20", context, 500.0)
        http, _ = client(policy=policy)
        payload = http.post("/checkout_sessions", json=create_body()).json()
        assert payload["totals"][1]["type"] == "items_discount"
        discount_line = payload["line_items"][0]
        assert discount_line["discount"] == 29988
        assert discount_line["total"] == 249900 - 29988

    def test_bandit_proposal_is_capped_by_gate_not_by_bandit(self):
        policy = fresh_policy()
        context = DecisionContext(
            session_id="x",
            target_sku="sku-hoodie",
            item_category="apparel",
            cart_value_inr=2499.0,
            buyer_allowance_inr=100000.0,
        )
        for _ in range(6):
            policy.update("d20", context, 500.0)
        http, _ = client(policy=policy)
        payload = http.post("/checkout_sessions", json=create_body()).json()
        discount_line = payload["line_items"][0]
        assert discount_line["discount"] <= 249900 * 0.15

    def test_unknown_item_yields_error_message_not_http_error(self):
        http, _ = client()
        response = http.post(
            "/checkout_sessions",
            json=create_body(items=[{"id": "sku-ghost", "quantity": 1}]),
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["status"] == "not_ready_for_payment"
        assert payload["messages"][0]["code"] == "invalid"

    def test_missing_allowance_blocks_readiness(self):
        body = create_body()
        del body["allowance"]
        payload = client()[0].post("/checkout_sessions", json=body).json()
        assert payload["status"] == "not_ready_for_payment"
        assert payload["messages"][0]["path"] == "$.allowance"


class TestOneOfferPerSession:
    def test_update_does_not_prompt_second_offer(self, tmp_path=None):
        http, audit = client()
        first = http.post("/checkout_sessions", json=create_body()).json()
        updated = http.post(f"/checkout_sessions/{first['id']}", json={"items": [{"id": "sku-tee", "quantity": 2}]}).json()
        assert updated["id"] == first["id"]
        assert updated["status"] == "ready_for_payment"


class TestComplete:
    def test_successful_completion_returns_order(self):
        http, audit = client()
        session = http.post("/checkout_sessions", json=create_body()).json()
        response = http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_ok", "provider": "razorpay"}},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        order = payload["order"]
        assert order["checkout_session_id"] == session["id"]

    def test_declined_payment_moves_to_needs_new_authorization(self):
        provider = ScriptedPaymentProvider({"tok_bad": PaymentOutcome.DECLINED})
        http, audit = client(provider=provider)
        session = http.post("/checkout_sessions", json=create_body()).json()
        payload = http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_bad"}},
        ).json()
        assert payload["status"] == "not_ready_for_payment"
        assert payload["messages"][0]["code"] == "payment_declined"

    def test_declined_payment_writes_failed_audit_entry_and_no_retry(self):
        provider = ScriptedPaymentProvider({"tok_bad": PaymentOutcome.DECLINED})
        http, audit = client(provider=provider)
        session = http.post("/checkout_sessions", json=create_body()).json()
        http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_bad"}},
        )
        statuses = [entry.outcome.status for entry in audit.iter_all()]
        assert statuses == ["failed"]
        assert len(provider.calls) == 1

    def test_expired_allowance_rejects_completion(self):
        http, audit = client()
        session = http.post(
            "/checkout_sessions",
            json=create_body(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
        ).json()
        payload = http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_ok"}},
        ).json()
        assert payload["status"] == "not_ready_for_payment"
        assert [e.outcome.status for e in audit.iter_all()] == ["failed"]

    def test_completed_session_is_idempotent(self):
        http, _ = client()
        session = http.post("/checkout_sessions", json=create_body()).json()
        first = http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_ok"}},
        )
        second = http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_ok"}},
        )
        assert first.json() == second.json()


class TestCancel:
    def test_cancel_records_declined_outcome(self):
        http, audit = client()
        session = http.post("/checkout_sessions", json=create_body()).json()
        payload = http.post(f"/checkout_sessions/{session['id']}/cancel").json()
        assert payload["status"] == "canceled"
        assert [e.outcome.status for e in audit.iter_all()] == ["declined"]

    def test_cancel_twice_conflicts(self):
        http, _ = client()
        session = http.post("/checkout_sessions", json=create_body()).json()
        http.post(f"/checkout_sessions/{session['id']}/cancel")
        response = http.post(f"/checkout_sessions/{session['id']}/cancel")
        assert response.status_code == 409

    def test_unknown_session_404(self):
        response = client()[0].get("/checkout_sessions/nope")
        assert response.status_code == 404


class TestAuditEveryPath:
    def test_every_gated_offer_is_audited(self):
        http, audit = client()
        session = http.post("/checkout_sessions", json=create_body()).json()
        http.post(
            f"/checkout_sessions/{session['id']}/complete",
            json={"payment_data": {"token": "tok_ok"}},
        )
        entries = list(audit.iter_all())
        assert len(entries) >= 1
        for entry in entries:
            assert entry.session_id == session["id"]


class TestBundleUpsellCharged:
    def test_fallback_bundle_is_charged_not_dropped(self):
        # No bandit -> rule-only fallback proposes a bundle_upsell. The charge
        # must include the add-on price, otherwise the upsell leaks revenue.
        provider = ScriptedPaymentProvider()
        http, _ = client(provider=provider)
        payload = http.post("/checkout_sessions", json=create_body()).json()
        assert payload.get("suggested_add_on") is not None
        add_on = payload["suggested_add_on"]
        cart_total = payload["totals"][0]["amount"]
        grand_total = payload["totals"][-1]["amount"]
        assert grand_total == cart_total + add_on["unit_amount"]

        response = http.post(
            f"/checkout_sessions/{payload['id']}/complete",
            json={"payment_data": {"token": "tok_ok"}},
        ).json()
        assert response["status"] == "completed"
        # The real charge must reflect the bundle, not just the cart base.
        assert provider.calls[-1][0] == grand_total
