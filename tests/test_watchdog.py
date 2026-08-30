import random

import pytest

from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.sessions import CheckoutSessionState, SessionRepository
from razorpay_agent.core.currency import INR
from razorpay_agent.decision import ContextEncoder, DiscountArm, LinUCBPolicy
from razorpay_agent.gate import RulePolicyGateConfig
from razorpay_agent.watchdog import SabotagedPolicy, SafetyWatchdog, SystemEventStore

CATEGORIES = ("apparel", "electronics")

GATE_CONFIG = RulePolicyGateConfig(
    fallback_bundle_item="sku-socks",
    fallback_bundle_price=499.0,
)


def make_watchdog(**overrides):
    defaults = dict(
        baseline_net_revenue_per_decision=250.0,
        baseline_gate_compliance_rate=0.87,
    )
    return SafetyWatchdog(**{**defaults, **overrides})


class TestTrigger:
    def test_sustained_bad_revenue_triggers_demotion(self):
        watchdog = make_watchdog()
        for _ in range(30):
            watchdog.observe_reward(5.0)
            watchdog.observe_gate_outcome(True)
        assert watchdog.demoted is True
        assert "net revenue" in watchdog.demotion_reason

    def test_sustained_noncompliance_alone_triggers(self):
        watchdog = make_watchdog()
        for _ in range(30):
            watchdog.observe_reward(300.0)
            watchdog.observe_gate_outcome(False)
        assert watchdog.demoted is True
        assert "compliance" in watchdog.demotion_reason

    def test_boundary_below_min_sample_never_triggers(self):
        watchdog = make_watchdog(min_sample=30)
        for _ in range(29):
            watchdog.observe_reward(0.0)
            watchdog.observe_gate_outcome(False)
        assert watchdog.demoted is False

    def test_recovery_within_window_prevents_trigger(self):
        watchdog = make_watchdog()
        for _ in range(15):
            watchdog.observe_reward(0.0)
        for _ in range(20):
            watchdog.observe_reward(280.0)
        assert watchdog.demoted is False


class TestNoFalsePositives:
    def test_normal_variance_over_long_run_never_triggers(self):
        rng = random.Random(42)
        watchdog = make_watchdog(min_sample=30)
        for _ in range(400):
            reward = rng.gauss(250.0, 120.0)
            watchdog.observe_reward(max(reward, 0.0))
            watchdog.observe_gate_outcome(rng.random() > 0.13)
        assert watchdog.demoted is False

    def test_moderate_dip_within_tolerance_does_not_trigger(self):
        watchdog = make_watchdog()
        for _ in range(40):
            watchdog.observe_reward(140.0)
            watchdog.observe_gate_outcome(True)
        assert watchdog.demoted is False


class TestPromote:
    def test_promote_requires_operator_note(self):
        watchdog = make_watchdog()
        for _ in range(30):
            watchdog.observe_reward(0.0)
        assert watchdog.demoted
        with pytest.raises(ValueError):
            watchdog.promote("   ")
        watchdog.promote("retrained and validated offline")
        assert watchdog.demoted is False
        assert watchdog.status()["rolling"]["reward_samples"] == 0

    def test_promote_when_not_demoted_rejected(self):
        with pytest.raises(RuntimeError):
            make_watchdog().promote("note")

    def test_observations_ignored_while_demoted(self):
        watchdog = make_watchdog()
        for _ in range(30):
            watchdog.observe_reward(0.0)
        assert watchdog.demoted
        watchdog.observe_reward(1.0)
        watchdog.observe_gate_outcome(False)
        status = watchdog.status()
        assert status["rolling"]["reward_samples"] == 30


class TestDemotionRoutesThroughFallbackPath:
    def _session(self, repo, session_id):
        from datetime import datetime, timedelta, timezone

        state = CheckoutSessionState(
            id=session_id,
            status="ready_for_payment",
            currency=INR,
            items=[{"product_id": "sku-hoodie", "quantity": 1}],
            allowance_max_paise=10_000_000,
            allowance_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        repo.save(state)
        return state

    def _pipeline_with_policy_and_watchdog(self, policy, watchdog):
        repo = SessionRepository()
        from razorpay_agent.audit import AuditStore

        pipeline = OfferPipeline(policy, GATE_CONFIG, AuditStore(":memory:"), watchdog=watchdog)
        return repo, pipeline

    def test_after_demotion_every_offer_is_fallback_rule_through_gate(self):
        policy = LinUCBPolicy(
            (DiscountArm("d10", 10.0),),
            ContextEncoder(CATEGORIES),
            alpha=0.5,
        )
        watchdog = make_watchdog()
        repo, pipeline = self._pipeline_with_policy_and_watchdog(policy, watchdog)

        before = pipeline.propose_for_session(
            self._session(repo, "s1"), 249900, "sku-hoodie", "apparel"
        )
        assert before.bandit_proposed is True
        assert before.proposed_action.source == "linucb_bandit"

        watchdog.demote("operator test")
        after = pipeline.propose_for_session(
            self._session(repo, "s2"), 249900, "sku-hoodie", "apparel"
        )
        assert after.bandit_proposed is False
        assert after.arm_id is None
        assert after.proposed_action.source == "fallback_rule"
        assert after.gate_decision.allowed in (True, False)
        assert after.discount_paise == 0

        assert pipeline._audit.count() >= 0

    def test_demoted_path_writes_audit_on_gate_rejection_identically(self):
        watchdog = make_watchdog()
        watchdog.demote("test")
        repo = SessionRepository()
        from razorpay_agent.audit import AuditStore

        pipeline = OfferPipeline(None, GATE_CONFIG, AuditStore(":memory:"), watchdog=watchdog)
        state = self._session(repo, "s3")
        state.allowance_max_paise = 50_000
        offer = pipeline.propose_for_session(state, 249900, "sku-hoodie", "apparel")
        assert offer.proposed_action.source == "fallback_rule"
        assert offer.gate_decision.allowed is False
        entries = list(pipeline._audit.iter_all())
        assert len(entries) == 1
        assert entries[0].outcome.status == "declined"
        assert entries[0].proposed_action.source == "fallback_rule"

    def test_resolution_rewards_forward_to_watchdog_only_for_bandit_offers(self):
        policy = LinUCBPolicy(
            (DiscountArm("d10", 10.0),),
            ContextEncoder(CATEGORIES),
            alpha=0.5,
        )
        watchdog = make_watchdog()
        repo, pipeline = self._pipeline_with_policy_and_watchdog(policy, watchdog)

        state = self._session(repo, "s4")
        pipeline.propose_for_session(state, 249900, "sku-hoodie", "apparel")
        pipeline.resolve_accepted(state, paid_total_paise=224910, base_total_paise=249900)
        samples_after_first = watchdog.status()["rolling"]["reward_samples"]
        assert samples_after_first >= 1

        state2 = self._session(repo, "s5")
        pipeline.propose_for_session(state2, 249900, "sku-hoodie", "apparel")
        samples_before_demote = watchdog.status()["rolling"]["reward_samples"]
        watchdog.demote("mid-test")
        pipeline.resolve_accepted(state2, paid_total_paise=224910, base_total_paise=249900)
        assert watchdog.status()["rolling"]["reward_samples"] == samples_before_demote
        assert watchdog.demoted is True


class TestSabotage:
    def test_sabotaged_policy_always_proposes_the_bad_arm(self):
        from razorpay_agent.decision import BundleArm

        inner = LinUCBPolicy(
            (DiscountArm("d5", 5.0), BundleArm("b_charger", "sku-charger", 1499.0)),
            ContextEncoder(CATEGORIES),
        )
        sabotaged = SabotagedPolicy(inner, "b_charger")
        arm_id, action = sabotaged.propose_with_arm(context())
        assert arm_id == "b_charger"
        assert action.bundle_item == "sku-charger"

    def test_unknown_bad_arm_rejected(self):
        inner = LinUCBPolicy((DiscountArm("d5", 5.0),), ContextEncoder(CATEGORIES))
        with pytest.raises(ValueError):
            SabotagedPolicy(inner, "ghost")


def context(session_id="sess-1"):
    from razorpay_agent.decision import DecisionContext

    return DecisionContext(
        session_id=session_id,
        target_sku="sku-1",
        item_category="apparel",
        cart_value_inr=2000.0,
        buyer_allowance_inr=8000.0,
    )


class TestSystemEvents:
    def test_events_round_trip(self, tmp_path):
        store = SystemEventStore(tmp_path / "events.sqlite3")
        store.record(component="watchdog", event_type="demotion", detail={"reason": "bad"})
        store.record(component="watchdog", event_type="promotion", detail={"note": "fixed"})
        events = store.recent(limit=10, component="watchdog")
        assert [e["event_type"] for e in events] == ["promotion", "demotion"]
        assert "bad" in events[1]["detail"]

    def test_on_demote_callback_receives_reason(self):
        captured = []
        watchdog = make_watchdog(on_demote=captured.append)
        for _ in range(30):
            watchdog.observe_reward(0.0)
        assert len(captured) == 1
        assert "net revenue" in captured[0]
