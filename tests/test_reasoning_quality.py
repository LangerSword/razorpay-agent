from __future__ import annotations

import json
from pathlib import Path

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.reasoning.agent import ReasoningAgent, _format_examples
from razorpay_agent.reasoning.llm import resolve_provider
from razorpay_agent.reasoning.store import ReasoningStore
from razorpay_agent.reasoning.tools import ReasoningDeps
from razorpay_agent.server import fresh_policy


def _agent(examples=None):
    cats = tuple(sorted({p.category for p in DEMO_CATALOG}))
    policy = fresh_policy(cats)
    gate = RulePolicyGateConfig(
        fallback_bundle_item="sku-socks", fallback_bundle_price=499.0
    )
    deps = ReasoningDeps(
        DEMO_CATALOG,
        policy,
        gate,
        regimen_graph=CoPurchaseGraph.from_catalog(DEMO_CATALOG),
    )
    return ReasoningAgent(
        llm=resolve_provider(),
        deps=deps,
        store=ReasoningStore(":memory:"),
        examples=examples,
    )


SCENARIOS = [
    {
        "name": "normal_discount",
        "target_sku": "sku-hoodie",
        "item_category": "apparel",
        "cart_value_inr": 2499.0,
        "buyer_allowance_inr": 100000.0,
        "is_stagnant": False,
        "bandit_action": {"action_type": "discount", "discount_percent": 10},
        "gate_decision": {"allowed": True},
    },
    {
        "name": "stagnant_clearance",
        "target_sku": "sku-oldstock",
        "item_category": "apparel",
        "cart_value_inr": 3999.0,
        "buyer_allowance_inr": 100000.0,
        "is_stagnant": True,
        "days_in_stock": 120,
        "bandit_action": {
            "action_type": "bundle_upsell",
            "bundle_item": "sku-hoodie",
            "bundle_price": 2499.0,
        },
        "gate_decision": {"allowed": True},
    },
]


def test_reasoning_examples_file_loads_when_present():
    examples_path = Path("demo/reasoning_examples.json")
    if not examples_path.exists():
        return
    data = json.loads(examples_path.read_text())
    examples = data.get("examples", [])
    assert isinstance(examples, list)
    for ex in examples:
        assert "turns" in ex or "final_text" in ex


def test_few_shot_formatting_round_trip():
    examples = [
        {
            "scenario": "normal_discount",
            "turns": [
                {"role": "assistant", "content": '<<tool:get_catalog_item {"sku": "sku-hoodie"}>>'},
                {"role": "user", "content": 'TOOL_RESULT: {"title": "Zip-Up Hoodie"}'},
                {"role": "assistant", "content": "The offer is sensible."},
            ],
            "final_text": "The offer is sensible.",
        }
    ]
    formatted = _format_examples(examples)
    assert "Example 1" in formatted
    assert "ASSISTANT:" in formatted
    assert "FINAL ANSWER:" in formatted


def test_agent_with_examples_does_not_crash():
    examples = [
        {
            "scenario": "normal_discount",
            "turns": [
                {"role": "assistant", "content": "The offer is sensible."},
            ],
            "final_text": "The offer is sensible.",
        }
    ]
    agent = _agent(examples=examples)
    assert agent._examples == examples


def test_reasoning_trace_efficiency():
    agent = _agent()
    scenario = SCENARIOS[0]
    result = agent.reason(
        "quality-normal",
        target_sku=scenario["target_sku"],
        item_category=scenario["item_category"],
        cart_value_inr=scenario["cart_value_inr"],
        buyer_allowance_inr=scenario["buyer_allowance_inr"],
        is_stagnant=scenario.get("is_stagnant", False),
        bandit_action=scenario.get("bandit_action"),
        gate_decision=scenario.get("gate_decision"),
    )
    tool_calls = [s for s in (result.steps or []) if "<<tool:" in (s.content or "")]
    assert len(tool_calls) <= 6, f"too many tool calls: {len(tool_calls)}"


def test_merchant_verdict_is_strict():
    """Verify merchant reasoner produces a strict verdict line."""
    from razorpay_agent.reasoning.llm import StubBackend
    agent = _agent()
    # Force stub to get deterministic behavior
    agent._llm = StubBackend()
    result = agent.reason(
        "strict-test",
        target_sku="sku-hoodie",
        item_category="apparel",
        cart_value_inr=2499.0,
        buyer_allowance_inr=100000.0,
        bandit_action={"action_type": "discount", "discount_percent": 10},
        gate_decision={"allowed": True},
    )
    # Should have a clear verdict
    assert result.verdict in ("APPROVE", "REJECT", "REVIEW")
    # Final text should contain the verdict line
    assert "Verdict:" in result.final_text
