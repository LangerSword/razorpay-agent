from __future__ import annotations

import tempfile

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.decision.arms import BundleArm
from razorpay_agent.decision.co_purchase_graph import (
    CoPurchaseGraph,
    candidate_bundles_for,
)
from razorpay_agent.decision.context import ContextEncoder
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.eval.replay import (
    EvalArm,
    SimSession,
    _action_to_sim_offer,
    sim_offer_for_arm,
)
from razorpay_agent.server import PRETRAINED_BANDIT_PATH

CATEGORIES = ("apparel", "electronics")


def _session(category):
    return SimSession(
        index=0,
        category=category,
        cart_value_rupees=2000.0,
        allowance_rupees=5000.0,
        base_completion_prob=0.5,
    )


class TestBundleArmAnchor:
    def test_anchor_sku_round_trips_through_snapshot(self):
        policy = LinUCBPolicy(
            [BundleArm("b1", "sku-socks", 499.0, anchor_sku="sku-hoodie")],
            ContextEncoder(CATEGORIES),
            alpha=0.5,
        )
        path = tempfile.mktemp(suffix=".json")
        policy.save(path)
        loaded = LinUCBPolicy.load(path)
        assert loaded._arms["b1"].anchor_sku == "sku-hoodie"

    def test_legacy_snapshot_loads_without_anchor(self):
        # The committed warm-start snapshot predates anchor_sku; it must still load,
        # with anchor_sku defaulting to None for bundle arms.
        loaded = LinUCBPolicy.load(PRETRAINED_BANDIT_PATH)
        for arm in loaded._arms.values():
            if isinstance(arm, BundleArm):
                assert arm.anchor_sku is None


class TestRegimenGraph:
    def test_degree_strength_relevance(self):
        g = CoPurchaseGraph.from_catalog(DEMO_CATALOG)
        assert g.degree("sku-hoodie") >= 1
        assert g.strength("sku-hoodie", "sku-socks") > 0
        # Within-category prior -> relevant set is the item's own category.
        assert g.relevant_categories("apparel") == {"apparel"}
        assert g.relevant_categories("electronics") == {"electronics"}

    def test_candidate_bundles_are_anchored(self):
        g = CoPurchaseGraph.from_catalog(DEMO_CATALOG)
        candidates = candidate_bundles_for("sku-hoodie", DEMO_CATALOG, g)
        assert candidates
        for arm in candidates:
            assert arm.anchor_sku == "sku-hoodie"
            assert arm.bundle_item in {e.target for e in g.neighbors("sku-hoodie")}


class TestSimulatorHonorsPrior:
    def test_bundle_relevance_is_graph_derived(self):
        g = CoPurchaseGraph.from_catalog(DEMO_CATALOG)
        # apparel bundle offered to an apparel session -> relevant
        same_cat = sim_offer_for_arm(
            EvalArm("b_socks", "bundle", bundle_item="sku-socks", bundle_price_rupees=499.0),
            _session("apparel"),
            regimen_graph=g,
        )
        assert same_cat.bundle_category_match is True
        # electronics bundle offered to an apparel session -> not relevant
        diff_cat = sim_offer_for_arm(
            EvalArm("b_charger", "bundle", bundle_item="sku-charger", bundle_price_rupees=1499.0),
            _session("apparel"),
            regimen_graph=g,
        )
        assert diff_cat.bundle_category_match is False

    def test_action_to_sim_offer_uses_graph(self):
        from razorpay_agent.core.actions import ProposedAction

        g = CoPurchaseGraph.from_catalog(DEMO_CATALOG)
        action = ProposedAction(
            action_type="bundle_upsell",
            target="sku-socks",
            expected_uplift=0.0,
            confidence=0.0,
            source="test",
            session_id="s",
            bundle_item="sku-socks",
            bundle_price=499.0,
        )
        offer = _action_to_sim_offer(action, _session("apparel"), regimen_graph=g)
        assert offer.bundle_category_match is True
