from __future__ import annotations

from datetime import UTC, datetime

from razorpay_agent.audit import AuditStore
from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.sessions import CheckoutSessionState
from razorpay_agent.core.currency import INR
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.graph.merchant_graph import MerchantAgentGraph
from razorpay_agent.server import fresh_policy

CATEGORIES = ("apparel", "electronics")
CART_PAISE = 249900  # sku-hoodie


def make_pipeline(policy):
    gate = RulePolicyGateConfig(
        fallback_bundle_item="sku-socks", fallback_bundle_price=499.0
    )
    audit = AuditStore(":memory:")
    return OfferPipeline(
        policy, gate, audit, watchdog=None, decision_log=None, temperature=0.0
    )


def make_session(is_stagnant=False, allowance=10_000_000):
    return CheckoutSessionState(
        id="sess",
        status="not_ready",
        currency=INR,
        items=[{"product_id": "sku-hoodie", "quantity": 1}],
        allowance_max_paise=allowance,
        allowance_expires_at=datetime.now(UTC),
        is_stagnant=is_stagnant,
        days_in_stock=120 if is_stagnant else None,
    )


def normalize(offer):
    dc = offer.decision_context
    return (
        offer.arm_id,
        offer.bandit_proposed,
        offer.discount_paise,
        offer.bundle_price_paise,
        offer.proposed_action.to_dict(),
        offer.gate_decision.to_dict(),
        (
            dc.session_id,
            dc.target_sku,
            dc.item_category,
            dc.cart_value_inr,
            dc.buyer_allowance_inr,
            dc.is_stagnant,
            dc.days_in_stock,
        ),
    )


def run_both(**session_kwargs):
    """Run the same session through the pipeline (direct) and the graph wrapper."""
    policy = fresh_policy(CATEGORIES)
    p_inline = make_pipeline(policy)
    p_graph = make_pipeline(policy)
    p_graph.attach_graph()

    s_inline = make_session(**session_kwargs)
    s_graph = make_session(**session_kwargs)

    offer_inline = p_inline.propose_for_session(s_inline, CART_PAISE, "sku-hoodie", "apparel")
    offer_graph = p_graph.propose_for_session(s_graph, CART_PAISE, "sku-hoodie", "apparel")
    return offer_inline, offer_graph


class TestGraphMatchesPipeline:
    def test_normal_session(self):
        inline, graph = run_both()
        assert inline is not None and graph is not None
        assert normalize(inline) == normalize(graph)

    def test_stagnant_session(self):
        inline, graph = run_both(is_stagnant=True)
        assert inline is not None and graph is not None
        assert normalize(inline) == normalize(graph)
        assert inline.gate_decision.allowed is True

    def test_rejected_session(self):
        inline, graph = run_both(allowance=10_000)  # far below projected total
        assert inline is not None and graph is not None
        assert normalize(inline) == normalize(graph)
        assert inline.gate_decision.allowed is False

    def test_fallback_session(self):
        # No bandit -> pipeline and graph both fall back to the rule-based action.
        inline, graph = run_both()
        # Force fallback by using a policy-less pipeline for both paths.
        p_inline = make_pipeline(None)
        p_graph = make_pipeline(None)
        p_graph.attach_graph()
        s_inline = make_session()
        s_graph = make_session()
        offer_inline = p_inline.propose_for_session(s_inline, CART_PAISE, "sku-hoodie", "apparel")
        offer_graph = p_graph.propose_for_session(s_graph, CART_PAISE, "sku-hoodie", "apparel")
        assert offer_inline is not None and offer_graph is not None
        assert normalize(offer_inline) == normalize(offer_graph)
        assert offer_inline.arm_id is None
        assert offer_inline.proposed_action.action_type == "bundle_upsell"

    def test_graph_is_attached_and_used(self):
        policy = fresh_policy(CATEGORIES)
        p_graph = make_pipeline(policy)
        assert p_graph._graph is None
        p_graph.attach_graph()
        assert p_graph._graph is not None
        # Early return when an offer already exists (mirrors the pipeline path).
        s = make_session()
        first = p_graph.propose_for_session(s, CART_PAISE, "sku-hoodie", "apparel")
        again = p_graph.propose_for_session(s, CART_PAISE, "sku-hoodie", "apparel")
        assert again is first


class TestCandidateGeneratorNode:
    def test_node_is_wired_into_graph(self):
        p = make_pipeline(fresh_policy(CATEGORIES))
        g = MerchantAgentGraph(p)
        assert "generate_candidates" in g._graph.nodes

    def test_candidate_arms_are_anchored(self):
        p = make_pipeline(fresh_policy(CATEGORIES))
        g = MerchantAgentGraph(p)
        arms = g.candidate_arms("sku-hoodie", DEMO_CATALOG, g._regimen_graph)
        assert arms, "expected regimen-anchored candidate bundles"
        assert all(a.anchor_sku == "sku-hoodie" for a in arms)

    def test_node_runs_without_altering_offer(self):
        # The graph still produces the same offer as the inline pipeline after the
        # candidate-generator node was inserted; offers remain unchanged.
        inline, graph = run_both()
        assert normalize(inline) == normalize(graph)
