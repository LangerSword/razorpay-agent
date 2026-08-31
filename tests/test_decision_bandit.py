import pytest

from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.decision import (
    BundleArm,
    ContextEncoder,
    DecisionContext,
    DiscountArm,
    InvalidDecisionInput,
    LinUCBPolicy,
)

CATEGORIES = ("apparel", "electronics")


def encoder():
    return ContextEncoder(CATEGORIES)


def policy(arms=None, alpha=1.0):
    default_arms = (
        DiscountArm("d5", 5.0),
        DiscountArm("d20", 20.0),
        BundleArm("b_addon", "sku-addon", 150.0),
    )
    return LinUCBPolicy(default_arms if arms is None else arms, encoder(), alpha=alpha)


def context(**overrides):
    defaults = dict(
        session_id="sess-1",
        target_sku="sku-1",
        item_category="apparel",
        cart_value_inr=2000.0,
        buyer_allowance_inr=5000.0,
    )
    return DecisionContext(**{**defaults, **overrides})


class TestProposal:
    def test_first_proposal_is_contract_valid(self):
        action = policy().propose(context())
        assert isinstance(action, ProposedAction)
        assert action.source == "linucb_bandit"
        assert action.session_id == "sess-1"
        assert 0.0 <= action.confidence <= 1.0

    def test_unexplored_proposal_has_zero_confidence(self):
        action = policy().propose(context())
        assert action.confidence == 0.0

    def test_ties_break_in_arm_order(self):
        action = policy(
            arms=(DiscountArm("first", 5.0), DiscountArm("second", 10.0))
        ).propose(context())
        assert action.discount_percent == 5.0

    def test_winning_arm_value_passes_through_uncapped(self):
        learned = policy(arms=(DiscountArm("small", 5.0), DiscountArm("aggressive", 20.0)))
        ctx = context()
        for _ in range(5):
            learned.update("aggressive", ctx, 500.0)
        action = learned.propose(ctx)
        assert action.discount_percent == 20.0

    def test_bundle_action_carries_catalog_fields(self):
        learned = policy(arms=(BundleArm("addon", "sku-addon", 250.0),))
        ctx = context()
        for _ in range(5):
            learned.update("addon", ctx, 300.0)
        action = learned.propose(ctx)
        assert action.action_type == "bundle_upsell"
        assert action.target == "sku-addon"
        assert action.bundle_item == "sku-addon"
        assert action.bundle_price == 250.0

    def test_discount_target_is_the_session_sku(self):
        action = policy(arms=(DiscountArm("d", 8.0),)).propose(context(target_sku="sku-7"))
        assert action.target == "sku-7"


class TestLearning:
    def test_rewarded_arm_beats_unexplored_alternatives(self):
        learned = policy()
        ctx = context()
        for _ in range(5):
            learned.update("d5", ctx, 100.0)
        action = learned.propose(ctx)
        assert action.discount_percent == 5.0
        assert action.expected_uplift > 0.0

    def test_confidence_rises_after_positive_evidence(self):
        learned = policy()
        ctx = context()
        for _ in range(5):
            learned.update("d5", ctx, 100.0)
        assert learned.propose(ctx).confidence > 0.0

    def test_sustained_negative_rewards_lead_to_abstention(self):
        learned = policy(arms=(DiscountArm("only", 10.0),))
        ctx = context()
        for _ in range(15):
            learned.update("only", ctx, -1000.0)
        assert learned.propose(ctx) is None

    def test_identical_policies_propose_identically(self):
        actions = []
        for _ in range(2):
            fresh = policy()
            ctx = context()
            fresh.update("d20", ctx, 50.0)
            actions.append(fresh.propose(ctx))
        assert actions[0] == actions[1]


class TestUpdateValidation:
    def test_unknown_arm_rejected(self):
        with pytest.raises(ValueError):
            policy().update("ghost", context(), 1.0)

    def test_non_numeric_reward_rejected(self):
        with pytest.raises(ValueError):
            policy().update("d5", context(), "big")

    def test_nan_reward_rejected(self):
        with pytest.raises(ValueError):
            policy().update("d5", context(), float("nan"))


class TestPolicyConstruction:
    def test_empty_arm_set_rejected(self):
        with pytest.raises(ValueError):
            LinUCBPolicy((), encoder())

    def test_duplicate_arm_ids_rejected(self):
        with pytest.raises(ValueError):
            LinUCBPolicy((DiscountArm("same", 5.0), DiscountArm("same", 8.0)), encoder())

    def test_non_positive_alpha_rejected(self):
        with pytest.raises(ValueError):
            policy(alpha=0.0)


class TestEncoderAndContextValidation:
    def test_unknown_category_rejected_at_encode_time(self):
        with pytest.raises(InvalidDecisionInput):
            policy().propose(context(item_category="grocery"))

    def test_duplicate_categories_rejected(self):
        with pytest.raises(InvalidDecisionInput):
            ContextEncoder(("apparel", "apparel"))

    def test_empty_categories_rejected(self):
        with pytest.raises(InvalidDecisionInput):
            ContextEncoder(())

    def test_dimension_tracks_category_count(self):
        # static features: intercept, cart/1000, allowance ratio, is_stagnant,
        # cart/1000 * days_in_stock/100 (clearance relief) and is_stagnant * cart/1000
        # (clearance penalty)
        assert encoder().dimension == len(CATEGORIES) + 6

    def test_non_positive_cart_value_rejected(self):
        with pytest.raises(InvalidDecisionInput):
            context(cart_value_inr=0.0)

    def test_blank_target_sku_rejected(self):
        with pytest.raises(InvalidDecisionInput):
            context(target_sku=" ")

    def test_arm_validation(self):
        with pytest.raises(ValueError):
            DiscountArm("bad", 101.0)
        with pytest.raises(ValueError):
            BundleArm("bad", "sku", 0.0)
