
import pytest

from razorpay_agent.decision import ContextEncoder, DiscountArm, LinUCBPolicy
from razorpay_agent.eval.offpolicy import estimate_candidate_alpha
from razorpay_agent.eval.storage import EvalStore

ARMS = (
    DiscountArm("d5", 5.0),
    DiscountArm("d10", 10.0),
    DiscountArm("d15", 15.0),
    DiscountArm("d20", 20.0),
)


def snapshot(tmp_path, alpha=0.5):
    policy = LinUCBPolicy(ARMS, ContextEncoder(("apparel", "electronics")), alpha=alpha)
    path = tmp_path / "snapshot.json"
    policy.save(path)
    return path


def fill_log(store: EvalStore, entries):
    """entries: list of (session_id, arm_id, reward)"""
    for index, (session_id, arm_id, reward) in enumerate(entries):
        store.log_decision(
            session_id=session_id,
            item_category="apparel",
            cart_value_rupees=2000.0,
            buyer_allowance_rupees=8000.0,
            target_sku="sku-hoodie",
            arm_id=arm_id,
            action_type="discount",
            discount_percent=float(arm_id[1:]),
            bundle_price_rupees=None,
            allowed_unmodified=True,
        )
        store.resolve_decision_reward(session_id, reward)


def repeated_entries(count):
    return [(f"s{i}", "d5", float(i % 11)) for i in range(count)]


class TestIdenticalAlpha:
    def test_same_alpha_reproduces_empirical_mean_exactly(self, tmp_path):
        store = EvalStore(":memory:")
        entries = repeated_entries(40)
        fill_log(store, entries)
        expected_mean = sum(r for _, _, r in entries) / len(entries)

        result = estimate_candidate_alpha(
            store,
            snapshot(tmp_path, alpha=0.5),
            alpha_candidate=0.5,
            min_ess=1.0,
        )

        assert result["verdict"] == "stable_estimate"
        assert result["estimated_net_revenue_per_decision"] == pytest.approx(expected_mean)
        assert result["effective_sample_size"] == pytest.approx(len(entries))
        assert result["standard_error"] >= 0.0

    def test_confidence_interval_brackets_point_estimate(self, tmp_path):
        store = EvalStore(":memory:")
        fill_log(store, repeated_entries(40))
        result = estimate_candidate_alpha(
            store, snapshot(tmp_path), alpha_candidate=0.5, min_ess=1.0
        )
        low, high = result["confidence_interval_95"]
        assert low <= result["estimated_net_revenue_per_decision"] <= high


class TestHonestyGuards:
    def test_too_few_decisions_refuses_to_estimate(self, tmp_path):
        store = EvalStore(":memory:")
        fill_log(store, repeated_entries(5))
        result = estimate_candidate_alpha(store, snapshot(tmp_path), alpha_candidate=0.25)
        assert result["verdict"] == "insufficient_logged_decisions"
        assert "estimated_net_revenue_per_decision" not in result
        assert "at least" in result["explanation"]

    def test_unreachable_ess_threshold_reports_degeneracy(self, tmp_path):
        store = EvalStore(":memory:")
        fill_log(store, repeated_entries(40))
        result = estimate_candidate_alpha(
            store,
            snapshot(tmp_path),
            alpha_candidate=0.25,
            min_ess=10_000.0,
        )
        assert result["verdict"] == "propensity_weights_too_degenerate"
        assert "estimated_net_revenue_per_decision" not in result

    def test_snapshot_drift_is_disclosed_not_hidden(self, tmp_path):
        store = EvalStore(":memory:")
        entries = [(f"s{i}", "d20", float(i % 5)) for i in range(40)]
        fill_log(store, entries)

        result = estimate_candidate_alpha(
            store, snapshot(tmp_path), alpha_candidate=0.5, min_ess=1.0
        )
        assert any("argmax matches only" in c for c in result.get("caveats", []))

    def test_reward_metric_label_names_aligned_convention(self, tmp_path):
        store = EvalStore(":memory:")
        fill_log(store, repeated_entries(40))
        result = estimate_candidate_alpha(
            store, snapshot(tmp_path), alpha_candidate=0.5, min_ess=1.0
        )
        assert "§4.7" in result["reward_metric"]


class TestUnresolvedRowsExcluded:
    def test_pending_rewards_do_not_count_toward_sample(self, tmp_path):
        store = EvalStore(":memory:")
        fill_log(store, repeated_entries(35))
        store.log_decision(
            session_id="pending-1",
            item_category="apparel",
            cart_value_rupees=2000.0,
            buyer_allowance_rupees=8000.0,
            target_sku="sku-hoodie",
            arm_id="d5",
            action_type="discount",
            discount_percent=5.0,
            bundle_price_rupees=None,
            allowed_unmodified=True,
        )
        assert store.logged_decision_count() == 35
