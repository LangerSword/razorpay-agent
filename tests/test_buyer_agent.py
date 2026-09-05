import asyncio

import httpx2 as httpx

from razorpay_agent.audit import AuditStore
from razorpay_agent.buyer import BuyerAgent
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
    BundleArm("b_socks", "sku-socks", 499.0),
)

GATE_CONFIG = RulePolicyGateConfig(
    fallback_bundle_item="sku-socks",
    fallback_bundle_price=499.0,
)


def build_stack(provider=None, policy=None, gate_config=None):
    audit = AuditStore(":memory:")
    repo = SessionRepository()
    pipeline = OfferPipeline(policy, gate_config or GATE_CONFIG, audit)
    app = build_app(DEMO_CATALOG, repo, pipeline, provider or ScriptedPaymentProvider())
    return app, audit


def make_buyer(app, **overrides):
    defaults = dict(
        base_url="http://testserver",
        transport=httpx.ASGITransport(app=app),
    )
    return BuyerAgent(**{**defaults, **overrides})


def run(coro):
    return asyncio.run(coro)


def trained_policy(arm_id="d20", cart_inr=2499.0):
    policy = LinUCBPolicy(ARMS, ContextEncoder(CATEGORIES), alpha=0.5)
    context = DecisionContext(
        session_id="warmup",
        target_sku="sku-hoodie",
        item_category="apparel",
        cart_value_inr=cart_inr,
        buyer_allowance_inr=100000.0,
    )
    for _ in range(6):
        policy.update(arm_id, context, 500.0)
    return policy


class TestEndToEndPurchase:
    def test_buyer_completes_purchase_over_real_acp_flow(self):
        app, _ = build_stack()
        agent = make_buyer(app)
        result = run(agent.run_purchase("sku-hoodie"))
        assert result.final_status == "completed"
        assert result.order is not None
        assert result.order["checkout_session_id"] == agent.last_session["id"]
        assert "discovered 15 products" in result.transcript[0]
        run(agent.aclose())

    def test_buyer_accepts_a_worthwhile_discount(self):
        app, audit = build_stack(policy=trained_policy("d10"))
        agent = make_buyer(app)
        result = run(agent.run_purchase("sku-hoodie"))
        assert result.accepted_offer is True
        assert any("-> accept" in line for line in result.transcript)
        assert [e.outcome.status for e in audit.iter_all()] == ["accepted"]
        run(agent.aclose())

    def test_buyer_declines_a_stingy_discount_but_still_buys(self):
        app, _ = build_stack(policy=trained_policy("d5"))
        agent = make_buyer(app, min_worthwhile_discount_percent=8.0)
        result = run(agent.run_purchase("sku-hoodie"))
        assert result.final_status == "completed"
        assert result.accepted_offer is False
        assert any("-> decline" in line for line in result.transcript)
        run(agent.aclose())

    def test_abstaining_bandit_falls_back_to_safe_default_offer(self):
        from razorpay_agent.reasoning.llm import StubBackend
        app, audit = build_stack(policy=None)
        agent = make_buyer(app, llm=StubBackend())
        result = run(agent.run_purchase("sku-hoodie"))
        assert result.final_status == "completed"
        assert result.accepted_offer is True
        entries = list(audit.iter_all())
        assert entries[0].proposed_action.source == "fallback_rule"
        assert entries[0].outcome.status == "accepted"
        run(agent.aclose())

    def test_allowance_below_cart_blocks_offers_and_completion(self):
        app, audit = build_stack()
        agent = make_buyer(app, max_allowance_paise=1_000)
        result = run(agent.run_purchase("sku-hoodie"))
        assert result.final_status == "canceled"
        assert any("mandate check failed" in line for line in result.transcript)
        run(agent.aclose())


class TestBuyerMandate:
    def test_expired_token_failure_is_not_retried(self):
        provider = ScriptedPaymentProvider({"tok_dead": PaymentOutcome.DECLINED})
        app, audit = build_stack(provider=provider)
        agent = make_buyer(app, payment_token="tok_dead")
        result = run(agent.run_purchase("sku-hoodie"))
        assert result.final_status == "not_ready_for_payment"
        assert len(provider.calls) == 1
        assert any("not retrying" in line for line in result.transcript)
        run(agent.aclose())

    def test_mandate_below_total_cancels_instead_of_completing(self):
        app, _ = build_stack()
        agent = make_buyer(app, max_allowance_paise=1_000)
        session = run(agent.start_session("sku-socks"))
        total = next(t["amount"] for t in session["totals"] if t["type"] == "total")
        assert total > 1_000
        completed = run(agent.complete_session(session["id"]))
        assert completed["status"] == "canceled"
        assert any("mandate check failed" in line for line in agent.transcript)
        run(agent.aclose())


class TestCancel:
    def test_buyer_can_cancel_cleanly(self):
        app, audit = build_stack()
        agent = make_buyer(app)
        session = run(agent.start_session("sku-tee"))
        canceled = run(agent.cancel_session(session["id"]))
        assert canceled["status"] == "canceled"
        run(agent.aclose())


class TestBuyerReasoning:
    def test_buyer_reasoning_gives_clear_verdict(self):
        """Verify buyer reasoner produces a clear ACCEPT/DECLINE verdict."""
        from razorpay_agent.buyer.reasoning_agent import PurchaseMemory, evaluate_offer
        from razorpay_agent.reasoning.llm import StubBackend

        # Session with a good discount (12% off sku-hoodie)
        session = {
            "target_sku": "sku-hoodie",
            "suggested_add_on": None,
            "line_items": [
                {
                    "item": {"id": "sku-hoodie", "quantity": 1},
                    "base_amount": 249900,
                    "discount": 29988,
                    "total": 219912,
                }
            ],
            "totals": [
                {"type": "items_base_amount", "amount": 249900},
                {"type": "items_discount", "amount": -29988},
                {"type": "subtotal", "amount": 219912},
                {"type": "total", "amount": 219912},
            ],
        }
        llm = StubBackend()
        verdict = evaluate_offer(
            llm=llm,
            session=session,
            cart_value_inr=2199.12,
            buyer_allowance_inr=100000.0,
            memory=PurchaseMemory(),
            min_discount_percent=5.0,
            max_add_on_share=0.25,
        )
        # Stub should accept a 12% discount
        assert verdict.verdict == "accept"
        assert "Verdict: ACCEPT" in verdict.rationale

    def test_buyer_reasoning_declines_stingy_offer(self):
        """Verify buyer reasoner declines a below-threshold discount."""
        from razorpay_agent.buyer.reasoning_agent import PurchaseMemory, evaluate_offer
        from razorpay_agent.reasoning.llm import StubBackend

        # Session with a 3% discount (below 5% minimum)
        session = {
            "target_sku": "sku-hoodie",
            "suggested_add_on": None,
            "line_items": [
                {
                    "item": {"id": "sku-hoodie", "quantity": 1},
                    "base_amount": 249900,
                    "discount": 7497,
                    "total": 242403,
                }
            ],
            "totals": [
                {"type": "items_base_amount", "amount": 249900},
                {"type": "items_discount", "amount": -7497},
                {"type": "subtotal", "amount": 242403},
                {"type": "total", "amount": 242403},
            ],
        }
        llm = StubBackend()
        verdict = evaluate_offer(
            llm=llm,
            session=session,
            cart_value_inr=2424.03,
            buyer_allowance_inr=100000.0,
            memory=PurchaseMemory(),
            min_discount_percent=5.0,
            max_add_on_share=0.25,
        )
        # Stub should decline 3% (below threshold)
        assert verdict.verdict == "decline"
        assert "Verdict: DECLINE" in verdict.rationale
