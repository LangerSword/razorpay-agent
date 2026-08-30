
import pytest

from razorpay_agent.core import DISCOUNT, ProposedAction
from razorpay_agent.gate import (
    BUYER_ALLOWANCE,
    FALLBACK_SOURCE,
    MAX_BUNDLE_SHARE,
    MAX_DISCOUNT_PCT,
    MAX_DISCOUNT_RUPEE_CAP,
    ONE_OFFER_PER_SESSION,
    InvalidContext,
    RulePolicyGate,
    RulePolicyGateConfig,
    SessionContext,
)

CONFIG = RulePolicyGateConfig(
    fallback_bundle_item="sku-classic",
    fallback_bundle_price=100.0,
)
GATE = RulePolicyGate(CONFIG)


def discount(percent, session_id="sess-1", source="linucb_bandit"):
    return ProposedAction(
        action_type=DISCOUNT,
        target="sku-1",
        expected_uplift=120.0,
        confidence=0.8,
        source=source,
        session_id=session_id,
        discount_percent=percent,
    )


def bundle(price, item="sku-2", session_id="sess-1"):
    return ProposedAction(
        action_type="bundle_upsell",
        target=item,
        expected_uplift=80.0,
        confidence=0.6,
        source="linucb_bandit",
        session_id=session_id,
        bundle_item=item,
        bundle_price=price,
    )


def context(**overrides):
    defaults = dict(session_id="sess-1", cart_value_inr=1000.0, buyer_allowance_inr=2000.0)
    return SessionContext(**{**defaults, **overrides})


class TestDiscountApproval:
    def test_clean_discount_passes_unchanged(self):
        decision = GATE.evaluate(discount(10.0), context())
        assert decision.allowed is True
        assert decision.final_action == discount(10.0)
        assert decision.checked_against == (MAX_DISCOUNT_PCT, MAX_DISCOUNT_RUPEE_CAP, BUYER_ALLOWANCE)

    def test_percent_over_cap_is_capped_not_rejected(self):
        decision = GATE.evaluate(discount(30.0), context())
        assert decision.allowed is True
        assert decision.final_action.discount_percent == 15.0
        assert decision.reason == "proposed 30% discount capped to 15%"

    def test_rupee_amount_over_cap_is_capped(self):
        decision = GATE.evaluate(
            discount(10.0), context(cart_value_inr=10_000.0, buyer_allowance_inr=20_000.0)
        )
        assert decision.allowed is True
        assert decision.final_action.discount_percent == 3.0
        assert "300.00" in decision.reason

    def test_discount_equal_to_cap_passes_exactly(self):
        decision = GATE.evaluate(discount(15.0), context())
        assert decision.final_action.discount_percent == 15.0
        assert "within all limits" in decision.reason

    def test_allowance_boundary_inclusive(self):
        decision = GATE.evaluate(discount(10.0), context(buyer_allowance_inr=900.0))
        assert decision.allowed is True

    def test_allowance_breach_rejects_entire_proposal(self):
        decision = GATE.evaluate(discount(10.0), context(buyer_allowance_inr=800.0))
        assert decision.allowed is False
        assert BUYER_ALLOWANCE in decision.checked_against
        assert decision.final_action.source == FALLBACK_SOURCE

    def test_tiny_cart_under_rupee_cap_leaves_no_meaningful_discount(self):
        tight_config = RulePolicyGateConfig(
            fallback_bundle_item="sku-classic",
            fallback_bundle_price=100.0,
            max_discount_rupee_cap=0.001,
        )
        decision = RulePolicyGate(tight_config).evaluate(discount(10.0), context())
        assert decision.allowed is False


class TestBundleApproval:
    def test_within_share_limit_passes_unchanged(self):
        decision = GATE.evaluate(bundle(150.0), context())
        assert decision.allowed is True
        assert decision.final_action == bundle(150.0)
        assert decision.checked_against == (MAX_BUNDLE_SHARE, BUYER_ALLOWANCE)

    def test_share_boundary_inclusive(self):
        decision = GATE.evaluate(bundle(200.0), context())
        assert decision.allowed is True

    def test_over_share_limit_rejected(self):
        decision = GATE.evaluate(bundle(250.0), context())
        assert decision.allowed is False
        assert decision.checked_against == (MAX_BUNDLE_SHARE,)
        assert decision.final_action.source == FALLBACK_SOURCE

    def test_allowance_breach_rejects_bundle(self):
        decision = GATE.evaluate(bundle(150.0), context(buyer_allowance_inr=1100.0))
        assert decision.allowed is False
        assert BUYER_ALLOWANCE in decision.checked_against


class TestOneOfferPerSession:
    def test_second_offer_rejected_even_if_perfectly_valid(self):
        decision = GATE.evaluate(discount(10.0), context(already_offered=True))
        assert decision.allowed is False
        assert decision.checked_against == (ONE_OFFER_PER_SESSION,)

    def test_second_bundle_offer_rejected_too(self):
        decision = GATE.evaluate(bundle(150.0), context(already_offered=True))
        assert decision.allowed is False
        assert ONE_OFFER_PER_SESSION in decision.checked_against


class TestFallback:
    def test_fallback_action_is_contract_valid_and_session_bound(self):
        decision = GATE.evaluate(bundle(900.0), context())
        fallback = decision.final_action
        assert isinstance(fallback, ProposedAction)
        assert fallback.action_type == "bundle_upsell"
        assert fallback.target == CONFIG.fallback_bundle_item
        assert fallback.source == FALLBACK_SOURCE
        assert fallback.session_id == "sess-1"
        assert fallback.bundle_price > 0

    def test_fallback_respects_share_limit_by_construction(self):
        decision = GATE.evaluate(bundle(5_000.0), context(cart_value_inr=400.0))
        share_limit = 0.20 * 400.0
        assert decision.final_action.bundle_price <= share_limit

    def test_reason_names_the_violation(self):
        decision = GATE.evaluate(bundle(900.0), context())
        assert decision.reason.startswith("rejected:")
        assert "cart value" in decision.reason


class TestContextValidation:
    def test_session_mismatch_between_action_and_context_rejected(self):
        with pytest.raises(InvalidContext):
            GATE.evaluate(discount(10.0, session_id="other"), context())

    def test_non_positive_cart_value_rejected(self):
        with pytest.raises(InvalidContext):
            context(cart_value_inr=0.0)

    def test_non_positive_allowance_rejected(self):
        with pytest.raises(InvalidContext):
            context(buyer_allowance_inr=-5.0)


class TestConfigValidation:
    def test_invalid_share_rejected(self):
        with pytest.raises(InvalidContext):
            RulePolicyGateConfig(fallback_bundle_item="x", fallback_bundle_price=1.0, max_bundle_cart_share=1.5)

    def test_negative_rupee_cap_rejected(self):
        with pytest.raises(InvalidContext):
            RulePolicyGateConfig(fallback_bundle_item="x", fallback_bundle_price=1.0, max_discount_rupee_cap=-1)

    def test_blank_fallback_item_rejected(self):
        with pytest.raises(InvalidContext):
            RulePolicyGateConfig(fallback_bundle_item="  ", fallback_bundle_price=1.0)


class TestRuleLayerSupremacy:
    def test_capped_discount_never_exceeds_the_cap(self):
        proposed = discount(30.0)
        decision = GATE.evaluate(proposed, context())
        assert decision.allowed is True
        assert decision.final_action.discount_percent == 15.0

    def test_rule_layer_output_is_what_audited_sessions_would_execute(self):
        proposed = discount(30.0)
        decision = GATE.evaluate(proposed, context())
        assert decision.final_action != proposed
