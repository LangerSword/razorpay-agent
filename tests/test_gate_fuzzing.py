import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from razorpay_agent.core import ContractViolation, ProposedAction
from razorpay_agent.gate import (
    BUYER_ALLOWANCE,
    MAX_BUNDLE_SHARE,
    MAX_DISCOUNT_PCT,
    MAX_DISCOUNT_RUPEE_CAP,
    ONE_OFFER_PER_SESSION,
    InvalidContext,
    RulePolicyGate,
    RulePolicyGateConfig,
    SessionContext,
)

EXAMPLES_PER_PROPERTY = int(os.environ.get("GATE_FUZZ_EXAMPLES", "300"))
FUZZ_SETTINGS = dict(max_examples=EXAMPLES_PER_PROPERTY, deadline=None)

finite_money = st.floats(
    min_value=0.01, max_value=1_000_000.0, allow_nan=False, allow_infinity=False
)
finite_positive = st.floats(min_value=0.001, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)

cart_strategy = st.one_of(
    finite_money,
    st.just(0.01),
    st.just(1499.0),
    st.just(2499.0),
    st.just(10_000.0),
)
discount_percent_strategy = st.one_of(
    st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False),
    st.sampled_from([0.01, 3.0, 12.0, 15.0, 15.0000001, 50.0, 99.99, 100.0]),
)
bundle_price_strategy = st.one_of(
    finite_positive,
    st.sampled_from([0.01, 499.0, 1499.0, 299.99]),
)


def config():
    return RulePolicyGateConfig(
        fallback_bundle_item="sku-socks",
        fallback_bundle_price=499.0,
    )


def gate():
    return RulePolicyGate(config())


def make_context(cart, allowance, already_offered=False, session_id="fuzz"):
    return SessionContext(
        session_id=session_id,
        cart_value_inr=cart,
        buyer_allowance_inr=allowance,
        already_offered=already_offered,
    )


def discount_action(percent, cart, session_id="fuzz"):
    return ProposedAction(
        action_type="discount",
        target="sku-fuzz",
        expected_uplift=0.0,
        confidence=0.5,
        source="linucb_bandit",
        session_id=session_id,
        discount_percent=percent,
    )


def bundle_action(price, cart, item="sku-charger", session_id="fuzz"):
    return ProposedAction(
        action_type="bundle_upsell",
        target=item,
        expected_uplift=0.0,
        confidence=0.5,
        source="linucb_bandit",
        session_id=session_id,
        bundle_item=item,
        bundle_price=price,
    )


TOLERANCE = 1e-6


def assert_discount_limits_hold(final, context):
    percent = float(final.discount_percent)
    assert 0.0 < percent <= config().max_discount_percent + TOLERANCE
    rupee_amount = context.cart_value_inr * percent / 100.0
    assert rupee_amount <= config().max_discount_rupee_cap + TOLERANCE
    projected_total = context.cart_value_inr - rupee_amount
    assert projected_total <= context.buyer_allowance_inr + TOLERANCE


def assert_bundle_limits_hold(final, context):
    price = float(final.bundle_price)
    assert price > 0.0
    share_limit = config().max_bundle_cart_share * context.cart_value_inr
    assert price <= share_limit + TOLERANCE
    assert context.cart_value_inr + price <= context.buyer_allowance_inr + TOLERANCE


class TestDiscountFuzzing:
    @settings(**FUZZ_SETTINGS)
    @given(
        cart=cart_strategy,
        percent=discount_percent_strategy,
        allowance=st.one_of(finite_money, st.just(0.01)),
        tight=st.booleans(),
    )
    def test_allowed_or_rejected_discount_never_violates_limits(
        self, cart, percent, allowance, tight
    ):
        if tight:
            effective = min(percent, config().max_discount_percent)
            best_case_rupee = min(
                cart * effective / 100.0, config().max_discount_rupee_cap
            )
            allowance = max(cart - best_case_rupee, 0.001)

        context = make_context(cart, allowance)
        decision = gate().evaluate(discount_action(percent, cart), context)

        assert decision.reason
        assert len(decision.checked_against) >= 1
        if decision.allowed:
            assert MAX_DISCOUNT_PCT in decision.checked_against
            assert MAX_DISCOUNT_RUPEE_CAP in decision.checked_against
            assert BUYER_ALLOWANCE in decision.checked_against
            assert_discount_limits_hold(decision.final_action, context)
        else:
            assert decision.final_action.source == "fallback_rule"
            fallback_price = float(decision.final_action.bundle_price)
            assert 0.0 < fallback_price


class TestBundleFuzzing:
    @settings(**FUZZ_SETTINGS)
    @given(
        cart=cart_strategy,
        price=bundle_price_strategy,
        allowance_mode=st.sampled_from(["exact_pass", "just_over", "random", "tiny"]),
        random_allowance=st.one_of(finite_money, st.just(0.01)),
    )
    def test_allowed_or_rejected_bundle_never_violates_limits(
        self, cart, price, allowance_mode, random_allowance
    ):
        share_limit = config().max_bundle_cart_share * cart
        if allowance_mode == "exact_pass":
            price = min(price, share_limit)
            allowance = cart + share_limit
        elif allowance_mode == "just_over":
            price = share_limit * (1 + 1e-9) + 1e-9
            allowance = cart + share_limit
        elif allowance_mode == "tiny":
            allowance = 0.01
        else:
            allowance = random_allowance

        context = make_context(cart, allowance)
        decision = gate().evaluate(bundle_action(price, cart), context)

        assert decision.reason
        if decision.allowed:
            assert MAX_BUNDLE_SHARE in decision.checked_against
            assert BUYER_ALLOWANCE in decision.checked_against
            assert_bundle_limits_hold(decision.final_action, context)
        else:
            assert decision.final_action.source == "fallback_rule"


class TestFallbackInvariants:
    @settings(**FUZZ_SETTINGS)
    @given(cart=cart_strategy, price=bundle_price_strategy, allowance=finite_money)
    def test_rejected_fallback_is_always_contract_valid_and_share_bounded(
        self, cart, price, allowance
    ):
        context = make_context(cart, allowance)
        decision = gate().evaluate(bundle_action(price, cart), context)
        if not decision.allowed:
            fallback = decision.final_action
            assert isinstance(fallback, ProposedAction)
            assert fallback.source == "fallback_rule"
            assert fallback.session_id == context.session_id
            assert float(fallback.bundle_price) > 0.0
            assert float(fallback.bundle_price) <= (
                config().max_bundle_cart_share * cart + TOLERANCE
            )

    @settings(**FUZZ_SETTINGS)
    @given(
        kind=st.sampled_from(["discount", "bundle"]),
        percent=discount_percent_strategy,
        price=bundle_price_strategy,
        cart=cart_strategy,
        allowance=finite_money,
    )
    def test_second_offer_is_always_rejected_regardless_of_content(
        self, kind, percent, price, cart, allowance
    ):
        context = make_context(cart, allowance, already_offered=True)
        action = (
            discount_action(percent, cart)
            if kind == "discount"
            else bundle_action(price, cart)
        )
        decision = gate().evaluate(action, context)
        assert decision.allowed is False
        assert ONE_OFFER_PER_SESSION in decision.checked_against
        assert decision.final_action.source == "fallback_rule"


class TestDegenerateInputs:
    @settings(**FUZZ_SETTINGS)
    @given(
        bad_cart=st.one_of(
            st.just(0.0),
            st.floats(max_value=-0.001, allow_nan=False),
            st.just(float("inf")),
            st.just(float("nan")),
        )
    )
    def test_non_positive_or_non_finite_cart_is_refused(self, bad_cart):
        with pytest.raises(InvalidContext):
            make_context(bad_cart, 1000.0)

    @settings(**FUZZ_SETTINGS)
    @given(
        bad_allowance=st.one_of(
            st.just(0.0),
            st.floats(max_value=-0.001, allow_nan=False),
            st.just(float("inf")),
            st.just(float("nan")),
        )
    )
    def test_non_positive_or_non_finite_allowance_is_refused(self, bad_allowance):
        with pytest.raises(InvalidContext):
            make_context(1000.0, bad_allowance)

    @settings(**FUZZ_SETTINGS)
    @given(
        percent=st.one_of(
            st.just(0.0),
            st.floats(max_value=0.0, exclude_max=True, allow_nan=False),
            st.floats(min_value=100.0001, max_value=1e6, allow_nan=False),
            st.just(float("nan")),
        )
    )
    def test_invalid_discount_percents_die_at_contract_layer(self, percent):
        with pytest.raises(ContractViolation):
            discount_action(percent, 2000.0)

    @settings(**FUZZ_SETTINGS)
    @given(
        bad_price=st.one_of(
            st.just(0.0),
            st.floats(max_value=-0.001, allow_nan=False),
            st.just(float("nan")),
        )
    )
    def test_invalid_bundle_prices_die_at_contract_layer(self, bad_price):
        with pytest.raises(ContractViolation):
            bundle_action(bad_price, 2000.0)


class TestConfigFuzzing:
    @settings(**FUZZ_SETTINGS)
    @given(
        share=st.one_of(
            st.floats(min_value=-10.0, max_value=0.0, allow_nan=False, allow_infinity=False),
            st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        )
    )
    def test_invalid_share_configurations_are_refused(self, share):
        with pytest.raises(InvalidContext):
            RulePolicyGateConfig(
                fallback_bundle_item="sku-socks",
                fallback_bundle_price=499.0,
                max_bundle_cart_share=share,
            )

    @settings(**FUZZ_SETTINGS)
    @given(
        bad_cap=st.one_of(
            st.just(0.0),
            st.floats(max_value=0.0, exclude_min=False, allow_nan=False),
            st.just(float("inf")),
        )
    )
    def test_invalid_rupee_caps_are_refused(self, bad_cap):
        with pytest.raises(InvalidContext):
            RulePolicyGateConfig(
                fallback_bundle_item="sku-socks",
                fallback_bundle_price=499.0,
                max_discount_rupee_cap=bad_cap,
            )
