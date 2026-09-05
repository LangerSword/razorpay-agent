from __future__ import annotations

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.reasoning.agent import ReasoningAgent
from razorpay_agent.reasoning.llm import (
    LLMBackend,
    StubBackend,
    get_provider,
    register_provider,
    resolve_provider,
)
from razorpay_agent.reasoning.store import ReasoningStore
from razorpay_agent.reasoning.tools import ReasoningDeps, build_registry
from razorpay_agent.server import fresh_policy

CATEGORIES = tuple(sorted({p.category for p in DEMO_CATALOG}))
GATE = RulePolicyGateConfig(fallback_bundle_item="sku-socks", fallback_bundle_price=499.0)


def deps(policy=None, graph=None):
    return ReasoningDeps(catalog=DEMO_CATALOG, policy=policy, gate_config=GATE, regimen_graph=graph)


def ctx_args(**over):
    base = dict(
        session_id="s",
        target_sku="sku-hoodie",
        item_category="apparel",
        cart_value_inr=2499.0,
        buyer_allowance_inr=100000.0,
        is_stagnant=False,
    )
    base.update(over)
    return base


class FailingBackend(LLMBackend):
    @property
    def name(self):
        return "failing"

    @property
    def model(self):
        return "failing-1"

    def complete(self, prompt: str) -> str:
        raise RuntimeError("boom")


class TestLLMFactory:
    def test_stub_emits_tool_then_final(self):
        b = StubBackend()
        first = b.complete("target_sku: sku-hoodie hello")
        assert "<<tool:get_catalog_item" in first
        second = b.complete("TOOL_RESULT: {...}")
        assert "<<tool:" not in second
        assert "proceed" in second.lower() or "approve" in second.lower()

    def test_resolve_default_is_stub(self, monkeypatch):
        import razorpay_agent.reasoning.llm as llm_mod

        monkeypatch.setattr(llm_mod, "load_dotenv_into_env", lambda *a, **k: None)
        monkeypatch.delenv("RAZORPAY_AGENT_LLM_PROVIDER", raising=False)
        assert resolve_provider().name == "stub"
        assert resolve_provider("nonexistent").name == "stub"

    def test_register_and_get_provider(self):
        class Temp(LLMBackend):
            @property
            def name(self):
                return "temp"

            @property
            def model(self):
                return "t"

            def complete(self, prompt: str) -> str:
                return ""

        register_provider("temp", Temp)
        assert get_provider("temp") is Temp


class TestTools:
    def test_all_five_tools(self):
        import json
        d = deps(policy=fresh_policy(CATEGORIES), graph=CoPurchaseGraph.from_catalog(DEMO_CATALOG))
        reg = build_registry(d)

        item = reg.call("get_catalog_item", {"sku": "sku-hoodie"})
        assert "Zip-Up Hoodie" in item

        policy = reg.call("get_clearance_policy", {})
        assert "clearance_max_discount_percent" in policy

        scores = reg.call("get_bandit_scores", ctx_args())
        assert isinstance(json.loads(scores), dict)

        est = reg.call(
            "estimate_outcome",
            {"offer": {"action_type": "discount", "discount_percent": 20},
             "session": {"cart_value_inr": 2499, "is_stagnant": False}},
        )
        assert json.loads(est)["completion_probability"] > 0.5

        graph = reg.call("get_regimen_graph", {"target_sku": "sku-hoodie"})
        assert isinstance(json.loads(graph), list)

    def test_error_wrapping_and_gating(self):
        d = deps(policy=None, graph=None)
        reg = build_registry(d)
        assert reg.call("nope", {}).startswith("ERROR")
        assert reg.call("get_bandit_scores", ctx_args()).startswith("ERROR[ToolUnavailable]")
        assert reg.call("get_regimen_graph", {"target_sku": "x"}).startswith("ERROR[ToolUnavailable]")
        assert reg.call("get_catalog_item", {"sku": "sku-missing"}).startswith("ERROR[")


class TestReasoningStore:
    def test_append_and_query(self):
        store = ReasoningStore(":memory:")
        store.append("s1", 1, "reasoning", "hello", provider="stub", model="m")
        store.append("s1", 2, "tool", "result", provider="stub", model="m")
        rows = store.for_session("s1")
        assert len(rows) == 2
        assert rows[0]["content"] == "hello"
        store.close()


class TestReasoningAgent:
    def test_loop_persists_and_explains(self):
        d = deps(policy=fresh_policy(CATEGORIES), graph=CoPurchaseGraph.from_catalog(DEMO_CATALOG))
        store = ReasoningStore(":memory:")
        agent = ReasoningAgent(llm=StubBackend(), deps=d, store=store)
        result = agent.reason(
            "sess-1",
            target_sku="sku-hoodie",
            item_category="apparel",
            cart_value_inr=2499.0,
            buyer_allowance_inr=100000.0,
            bandit_action={"action_type": "discount", "discount_percent": 10},
            gate_decision={"allowed": True},
        )
        assert result.provider == "stub"
        assert result.fallback is False
        # Stub may shortcut to 1 step if it detects a final answer pattern
        assert len(result.steps) >= 1
        assert "proceed" in result.final_text.lower() or "approve" in result.final_text.lower()
        assert len(store.for_session("sess-1")) == len(result.steps)
        store.close()

    def test_fallback_when_llm_fails(self):
        d = deps(policy=fresh_policy(CATEGORIES))
        store = ReasoningStore(":memory:")
        agent = ReasoningAgent(llm=FailingBackend(), deps=d, store=store)
        result = agent.reason(
            "sess-2",
            target_sku="sku-hoodie",
            item_category="apparel",
            cart_value_inr=2499.0,
            buyer_allowance_inr=100000.0,
        )
        assert result.fallback is True
        assert "bandit" in result.final_text.lower()
        store.close()

    def test_runs_without_policy_no_llm_path_intact(self):
        d = deps(policy=None, graph=None)
        agent = ReasoningAgent(llm=StubBackend(), deps=d)
        result = agent.reason(
            "sess-3",
            target_sku="sku-oldstock",
            item_category="apparel",
            cart_value_inr=3999.0,
            buyer_allowance_inr=100000.0,
            is_stagnant=True,
            days_in_stock=120,
        )
        assert result.fallback is False
        assert len(result.steps) >= 1
