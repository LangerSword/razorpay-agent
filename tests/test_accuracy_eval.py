"""Tests for agent accuracy grading — buyer verdicts + merchant reasoning."""

from __future__ import annotations

from razorpay_agent.eval.accuracy import (
    grade_buyer_verdict,
    grade_merchant_reasoning,
    run_buyer_accuracy_eval,
    run_merchant_reasoning_eval,
)
from razorpay_agent.reasoning.llm import StubBackend


class TestBuyerVerdictGrading:
    def test_accept_meets_threshold(self):
        g = grade_buyer_verdict(
            "accept", "discount", discount_percent=10.0,
            cart_value_inr=2499.0, min_discount_percent=5.0,
        )
        assert g.correct is True
        assert g.expected == "accept"

    def test_decline_below_threshold(self):
        g = grade_buyer_verdict(
            "decline", "discount", discount_percent=3.0,
            cart_value_inr=2499.0, min_discount_percent=5.0,
        )
        assert g.correct is True

    def test_wrong_verdict_is_incorrect(self):
        g = grade_buyer_verdict(
            "decline", "discount", discount_percent=20.0,
            cart_value_inr=2499.0, min_discount_percent=5.0,
        )
        assert g.correct is False
        assert g.expected == "accept"

    def test_bundle_within_share(self):
        g = grade_buyer_verdict(
            "accept", "bundle", add_on_price_inr=200.0,
            cart_value_inr=2499.0, max_add_on_share=0.25,
        )
        assert g.correct is True

    def test_bundle_over_share(self):
        g = grade_buyer_verdict(
            "decline", "bundle", add_on_price_inr=700.0,
            cart_value_inr=2499.0, max_add_on_share=0.25,
        )
        assert g.correct is True

    def test_approve_accept_aliases(self):
        """APPROVE/REJECT should map to accept/decline."""
        g = grade_buyer_verdict(
            "approve", "discount", discount_percent=10.0,
            cart_value_inr=2499.0, min_discount_percent=5.0,
        )
        assert g.correct is True


class TestMerchantReasoningGrading:
    def test_stub_reasoning_passes_all(self):
        """StubBackend reasoning should pass all grading dimensions."""
        g = grade_merchant_reasoning(
            "- Proposed discount: 10% on INR 2499.00 cart\n"
            "- Gate action: allowed as-is (within 15% + 300 INR limits)\n"
            "- Cart value: INR 2499.00 -> discount amount: INR 249.90\n"
            "- Policy check: within 15% max discount, within 300 INR cap\n"
            "Verdict: APPROVE",
            bandit_discount_percent=10.0,
            gate_allowed=True,
            max_discount_percent=15.0,
            max_discount_rupee_cap=300.0,
        )
        assert g.arm_identified is True
        assert g.gate_aware is True
        assert g.limits_accurate is True
        assert g.verdict_correct is True
        assert g.correct is True

    def test_misses_arm_identification(self):
        g = grade_merchant_reasoning(
            "- Gate action: allowed as-is\n"
            "Verdict: APPROVE",
            bandit_discount_percent=25.0,
            gate_allowed=True,
        )
        assert g.arm_identified is False

    def test_misses_gate_capped(self):
        g = grade_merchant_reasoning(
            "- Proposed discount: 10% on INR 9998.00 cart\n"
            "- Gate action: allowed as-is\n"  # Should say capped
            "Verdict: APPROVE",
            bandit_discount_percent=10.0,
            gate_allowed=True,
            gate_capped=True,
        )
        assert g.gate_aware is False

    def test_misses_verdict_when_rejected(self):
        g = grade_merchant_reasoning(
            "- Proposed discount: 35% on INR 3999.00 cart\n"
            "Verdict: APPROVE",  # Should be REJECT
            bandit_discount_percent=35.0,
            gate_allowed=False,
        )
        assert g.verdict_correct is False


class TestBuyerAccuracyEval:
    def test_stub_buyer_sweeps_scenarios(self):
        """StubBackend buyer should get most scenarios correct."""
        summary = run_buyer_accuracy_eval(StubBackend())
        assert summary.total == 10
        assert summary.accuracy >= 0.7
        assert "discount" in summary.by_offer_type
        assert "bundle" in summary.by_offer_type

    def test_buyer_has_no_systematic_bias(self):
        """Both discount and bundle scenarios should be handled."""
        summary = run_buyer_accuracy_eval(StubBackend())
        for offer_type, metrics in summary.by_offer_type.items():
            assert metrics["accuracy"] >= 0.5, f"{offer_type} below 50% accuracy"


class TestMerchantReasoningEval:
    def test_stub_merchants_sweeps_scenarios(self):
        summary = run_merchant_reasoning_eval(StubBackend())
        assert summary.total == 4
        assert summary.arm_identification_rate >= 0.5
        assert summary.verdict_accuracy_rate >= 0.5

    def test_stub_grades_above_zero(self):
        summary = run_merchant_reasoning_eval(StubBackend())
        assert summary.accuracy > 0.0  # At least some dimensions pass
