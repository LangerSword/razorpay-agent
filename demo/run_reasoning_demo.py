from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.reasoning.agent import ReasoningAgent
from razorpay_agent.reasoning.llm import resolve_provider
from razorpay_agent.reasoning.store import ReasoningStore
from razorpay_agent.reasoning.tools import ReasoningDeps
from razorpay_agent.server import fresh_policy

GATE = RulePolicyGateConfig(fallback_bundle_item="sku-socks", fallback_bundle_price=499.0)
CATS = tuple(sorted({p.category for p in DEMO_CATALOG}))
deps = ReasoningDeps(
    DEMO_CATALOG,
    fresh_policy(CATS),
    GATE,
    CoPurchaseGraph.from_catalog(DEMO_CATALOG),
)
examples_path = Path("demo/reasoning_examples.json")
examples = None
if examples_path.exists():
    try:
        examples = json.loads(examples_path.read_text()).get("examples", [])
    except Exception:
        examples = None
agent = ReasoningAgent(
    llm=resolve_provider(), deps=deps, store=ReasoningStore(":memory:"), examples=examples
)


def show(title: str, **kw) -> None:
    print(f"\n=== {title} ===")
    res = agent.reason("demo-session", **kw)
    for s in res.steps:
        print(f"  [{s.step}] {s.role}: {s.content[:160]}")
    print(f"  -> provider={res.provider} fallback={res.fallback}")


show(
    "normal offer",
    target_sku="sku-hoodie",
    item_category="apparel",
    cart_value_inr=2499.0,
    buyer_allowance_inr=100000.0,
    bandit_action={"action_type": "discount", "discount_percent": 10},
    gate_decision={"allowed": True},
)
show(
    "stagnant clearance",
    target_sku="sku-oldstock",
    item_category="apparel",
    cart_value_inr=3999.0,
    buyer_allowance_inr=100000.0,
    is_stagnant=True,
    days_in_stock=120,
    bandit_action={"action_type": "bundle_upsell", "bundle_item": "sku-socks", "bundle_price": 499.0},
    gate_decision={"allowed": True},
)
print("\n[reasoning-demo] done; trace persisted to reasoning_log (keyless StubBackend)")
