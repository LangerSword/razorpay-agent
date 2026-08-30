import pytest

from razorpay_agent.decision import (
    BundleArm,
    ContextEncoder,
    DecisionContext,
    DiscountArm,
    LinUCBPolicy,
)


def arms():
    return (
        DiscountArm("d5", 5.0),
        DiscountArm("d20", 20.0),
        BundleArm("b_socks", "sku-socks", 499.0),
    )


def context(session_id="sess-1"):
    return DecisionContext(
        session_id=session_id,
        target_sku="sku-1",
        item_category="apparel",
        cart_value_inr=2000.0,
        buyer_allowance_inr=8000.0,
    )


def trained_policy():
    policy = LinUCBPolicy(arms(), ContextEncoder(("apparel", "electronics")), alpha=0.5)
    for _ in range(7):
        policy.update("d20", context(), 500.0)
    for _ in range(3):
        policy.update("b_socks", context(), 100.0)
    return policy


class TestSaveLoadRoundTrip:
    def test_loaded_policy_proposes_identically(self, tmp_path):
        original = trained_policy()
        path = tmp_path / "bandit.json"
        original.save(path)

        loaded = LinUCBPolicy.load(path)
        first = original.propose(context())
        second = loaded.propose(context())
        assert first == second
        assert first.discount_percent == 20.0

    def test_state_dict_carries_provenance(self, tmp_path):
        policy = trained_policy()
        state = policy.to_state_dict()
        assert state["format_version"] == 1
        assert state["alpha"] == 0.5
        assert state["trained_sessions"] == 10
        assert {arm["arm_id"] for arm in state["arms"]} == {"d5", "d20", "b_socks"}
        assert set(state["A"]) == {"d5", "d20", "b_socks"}

    def test_loaded_state_keeps_learning(self, tmp_path):
        path = tmp_path / "bandit.json"
        trained_policy().save(path)
        loaded = LinUCBPolicy.load(path)
        before = loaded.propose(context()).confidence
        for _ in range(5):
            loaded.update("d5", context(), -900.0)
        after = loaded.propose(context())
        assert after.discount_percent == 20.0 and after.confidence >= before


class TestLoadValidation:
    def test_unknown_format_rejected(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"format_version": 999}')
        with pytest.raises(ValueError):
            LinUCBPolicy.load(path)

    def test_dimension_mismatch_rejected(self, tmp_path):
        policy = trained_policy()
        state = policy.to_state_dict()
        state["categories"] = ["apparel", "electronics", "grocery"]
        path = tmp_path / "mismatch.json"
        path.write_text(__import__("json").dumps(state))
        with pytest.raises(ValueError):
            LinUCBPolicy.load(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LinUCBPolicy.load(tmp_path / "nope.json")


class TestDemoTokenRouting:
    def test_decline_token_never_hits_razorpay_api(self):
        class ExplodingClient:
            def order(self):
                raise AssertionError("API must not be called")

        provider = _provider_with_client_guard()
        result = provider.charge(100, "inr", "tok_declined")
        assert result.provider_reference.startswith("demo_declined")

    def test_expired_token_routed_without_api_call(self):
        provider = _provider_with_client_guard()
        result = provider.charge(100, "inr", "tok_expired")
        assert result.outcome.value == "expired_token"


def _provider_with_client_guard():
    from razorpay_agent.checkout.payments import RazorpayTestProvider

    class GuardedClient:
        def __getattr__(self, name):
            raise AssertionError(f"razorpay API touched via {name}")

    return RazorpayTestProvider("k", "s", client=GuardedClient())
