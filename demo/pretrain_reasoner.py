from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.reasoning.agent import ReasoningAgent
from razorpay_agent.reasoning.llm import resolve_provider
from razorpay_agent.reasoning.store import ReasoningStore
from razorpay_agent.reasoning.tools import ReasoningDeps
from razorpay_agent.server import fresh_policy

DEFAULT_OUT = "demo/reasoning_examples.json"
DEFAULT_SCENARIOS = 12


def _pipeline() -> OfferPipeline:
    cats = tuple(sorted({p.category for p in DEMO_CATALOG}))
    policy = fresh_policy(cats)
    gate = RulePolicyGateConfig(
        fallback_bundle_item="sku-socks",
        fallback_bundle_price=499.0,
    )
    audit_path = Path(":memory:")
    return OfferPipeline(policy, gate, audit_path)


def _agent(store_path: str | None = None, examples=None) -> ReasoningAgent:
    pipeline = _pipeline()
    deps = ReasoningDeps(
        catalog=DEMO_CATALOG,
        policy=pipeline._policy,
        gate_config=pipeline._gate_config,
        regimen_graph=CoPurchaseGraph.from_catalog(DEMO_CATALOG),
    )
    store = ReasoningStore(store_path or ":memory:")
    return ReasoningAgent(
        llm=resolve_provider(), deps=deps, store=store, examples=examples, max_steps=4
    )


SCENARIOS: list[dict[str, Any]] = [
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
        "name": "normal_bundle",
        "target_sku": "sku-hoodie",
        "item_category": "apparel",
        "cart_value_inr": 2499.0,
        "buyer_allowance_inr": 100000.0,
        "is_stagnant": False,
        "bandit_action": {
            "action_type": "bundle_upsell",
            "bundle_item": "sku-socks",
            "bundle_price": 499.0,
        },
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
    {
        "name": "rejected_allowance",
        "target_sku": "sku-headphones",
        "item_category": "electronics",
        "cart_value_inr": 4999.0,
        "buyer_allowance_inr": 1000.0,
        "is_stagnant": False,
        "bandit_action": {"action_type": "discount", "discount_percent": 15},
        "gate_decision": {"allowed": False, "reason": "buyer allowance exceeded"},
    },
    {
        "name": "gate_capped_discount",
        "target_sku": "sku-hoodie",
        "item_category": "apparel",
        "cart_value_inr": 2499.0,
        "buyer_allowance_inr": 100000.0,
        "is_stagnant": False,
        "bandit_action": {"action_type": "discount", "discount_percent": 25},
        "gate_decision": {"allowed": True, "capped_from": 25.0, "capped_to": 15.0},
    },
]


def _score_trace(trace: dict[str, Any]) -> float:
    score = 0.0
    if trace.get("fallback") is False:
        score += 0.4
    final = (trace.get("final_text") or "").strip()
    if len(final) > 50:
        score += 0.3
    steps = trace.get("steps") or []
    if steps and all("ERROR" not in (s.get("content") or "") for s in steps):
        score += 0.2
    if len(steps) <= 6:
        score += 0.1
    return score


def _to_few_shot(trace: dict[str, Any]) -> dict[str, Any] | None:
    if trace.get("fallback") is True:
        return None
    final = (trace.get("final_text") or "").strip()
    if not final or len(final) < 80:
        return None
    low = final.lower()
    if low.startswith("<<tool:") or "error" in low or "unavailable" in low:
        return None
    turns: list[dict[str, Any]] = []
    for step in trace.get("steps") or []:
        role = step.get("role")
        content = (step.get("content") or "").strip()
        if not content:
            continue
        if role == "reasoning" and content.startswith("<<tool:"):
            turns.append({"role": "assistant", "content": content})
        elif role == "tool":
            turns.append({"role": "user", "content": f"TOOL_RESULT: {content}"})
        elif role == "reasoning" and not content.startswith("<<"):
            turns.append({"role": "assistant", "content": content})
    if not turns:
        return None
    return {
        "scenario": trace.get("session_id"),
        "turns": turns,
        "final_text": final,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="pretrain the reasoning agent over live traces")
    parser.add_argument("--scenarios", type=int, default=DEFAULT_SCENARIOS)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--out", type=str, default=DEFAULT_OUT)
    parser.add_argument("--min-score", type=float, default=0.7)
    parser.add_argument("--verbose", action="store_true", default=False)
    args = parser.parse_args()

    store_path = ":memory:"
    agent = _agent(store_path)
    raw: list[dict[str, Any]] = []

    pool = SCENARIOS * max(1, args.scenarios // len(SCENARIOS))
    for idx, scenario in enumerate(pool[: args.scenarios]):
        result = agent.reason(
            f"pretrain-{scenario['name']}",
            target_sku=scenario["target_sku"],
            item_category=scenario["item_category"],
            cart_value_inr=scenario["cart_value_inr"],
            buyer_allowance_inr=scenario["buyer_allowance_inr"],
            is_stagnant=scenario.get("is_stagnant", False),
            days_in_stock=scenario.get("days_in_stock"),
            bandit_action=scenario.get("bandit_action"),
            gate_decision=scenario.get("gate_decision"),
        )
        trace = {
            "session_id": result.session_id,
            "provider": result.provider,
            "model": result.model,
            "fallback": result.fallback,
            "steps": [
                {
                    "step": s.step,
                    "role": s.role,
                    "content": s.content,
                    "provider": s.provider,
                    "model": s.model,
                }
                for s in result.steps
            ],
            "final_text": result.final_text,
            "scenario": scenario["name"],
        }
        raw.append(trace)

        if args.verbose:
            score = _score_trace(trace)
            few = _to_few_shot(trace)
            print(f"\n=== {result.session_id} ===")
            print(f"  fallback={result.fallback}  score={score:.2f}  selectable={few is not None}")
            for s in trace["steps"]:
                print(f"  [{s['step']}] {s['role']}: {s['content'][:180]}")
            print(f"  final: {(trace['final_text'] or '')[:200]}")

        import time

        time.sleep(2)

    scored = [
        (trace, _score_trace(trace))
        for trace in raw
    ]
    scored.sort(key=lambda pair: pair[1], reverse=True)

    selected: list[dict[str, Any]] = []
    for trace, score in scored:
        if score < args.min_score:
            continue
        few_shot = _to_few_shot(trace)
        if few_shot is not None:
            selected.append(few_shot)

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": agent._llm.name,
        "provider": agent._llm.name,
        "scenario_count": len(raw),
        "selected_count": len(selected),
        "min_score": args.min_score,
        "examples": selected[:20],
    }

    Path(args.out).write_text(json.dumps(out, indent=2))
    print(
        f"[pretrain_reasoner] scenarios={len(raw)} "
        f"selected={len(selected)} -> {args.out}"
    )
    for trace, score in scored[:5]:
        print(
            f"  {trace['session_id']:40s}  "
            f"score={score:.2f}  fallback={trace['fallback']}  "
            f"steps={len(trace['steps'])}"
        )


if __name__ == "__main__":
    main()
