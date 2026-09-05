from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.gate.gate import RulePolicyGateConfig
from razorpay_agent.reasoning.agent import ReasoningAgent, ReasoningResult
from razorpay_agent.reasoning.llm import resolve_provider
from razorpay_agent.reasoning.store import ReasoningStore
from razorpay_agent.reasoning.tools import ReasoningDeps
from razorpay_agent.server import fresh_policy
from razorpay_agent.buyer.reasoning_agent import (
    evaluate_offer,
    PurchaseMemory,
    _summarize_offer,
)

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


def _merchant_agent(store_path: str | None = None, examples=None) -> ReasoningAgent:
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


def _buyer_agent():
    """Return the LLM used for buyer reasoning (no special agent — buyer uses evaluate_offer directly)."""
    return resolve_provider()


MERCHANT_SCENARIOS: list[dict[str, Any]] = [
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


BUYER_SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "good_discount",
        "session": {
            "target_sku": "sku-hoodie",
            "suggested_add_on": None,
            "line_items": [
                {
                    "item": {"id": "sku-hoodie", "quantity": 1},
                    "base_amount": 249900,
                    "discount": 29988,
                    "total": 219912,
                }
            ],
            "totals": [
                {"type": "items_base_amount", "amount": 249900},
                {"type": "items_discount", "amount": -29988},
                {"type": "subtotal", "amount": 219912},
                {"type": "total", "amount": 219912},
            ],
        },
        "cart_value_inr": 2199.12,
        "buyer_allowance_inr": 100000.0,
        "memory": PurchaseMemory(),
        "min_discount_percent": 5.0,
        "max_add_on_share": 0.25,
    },
    {
        "name": "stingy_discount",
        "session": {
            "target_sku": "sku-hoodie",
            "suggested_add_on": None,
            "line_items": [
                {
                    "item": {"id": "sku-hoodie", "quantity": 1},
                    "base_amount": 249900,
                    "discount": 12495,
                    "total": 237405,
                }
            ],
            "totals": [
                {"type": "items_base_amount", "amount": 249900},
                {"type": "items_discount", "amount": -12495},
                {"type": "subtotal", "amount": 237405},
                {"type": "total", "amount": 237405},
            ],
        },
        "cart_value_inr": 2374.05,
        "buyer_allowance_inr": 100000.0,
        "memory": PurchaseMemory(),
        "min_discount_percent": 5.0,
        "max_add_on_share": 0.25,
    },
    {
        "name": "bundle_upsell_relevant",
        "session": {
            "target_sku": "sku-hoodie",
            "suggested_add_on": {"item_id": "sku-socks", "unit_amount": 49900},
            "line_items": [
                {
                    "item": {"id": "sku-hoodie", "quantity": 1},
                    "base_amount": 249900,
                    "discount": 0,
                    "total": 249900,
                }
            ],
            "totals": [
                {"type": "items_base_amount", "amount": 249900},
                {"type": "add_on", "amount": 49900},
                {"type": "subtotal", "amount": 299800},
                {"type": "total", "amount": 299800},
            ],
        },
        "cart_value_inr": 2499.00,
        "buyer_allowance_inr": 100000.0,
        "memory": PurchaseMemory(),
        "min_discount_percent": 5.0,
        "max_add_on_share": 0.25,
    },
    {
        "name": "no_offer",
        "session": {
            "target_sku": "sku-hoodie",
            "suggested_add_on": None,
            "line_items": [
                {
                    "item": {"id": "sku-hoodie", "quantity": 1},
                    "base_amount": 249900,
                    "discount": 0,
                    "total": 249900,
                }
            ],
            "totals": [
                {"type": "items_base_amount", "amount": 249900},
                {"type": "subtotal", "amount": 249900},
                {"type": "total", "amount": 249900},
            ],
        },
        "cart_value_inr": 2499.00,
        "buyer_allowance_inr": 100000.0,
        "memory": PurchaseMemory(),
        "min_discount_percent": 5.0,
        "max_add_on_share": 0.25,
    },
]


def _score_trace(trace: dict[str, Any]) -> float:
    score = 0.0
    if trace.get("fallback") is False:
        score += 0.3
    final = (trace.get("final_text") or "").strip()
    if not final:
        return 0.0
    # Check if final_text is mostly tool calls (garbage)
    lines = [l.strip() for l in final.split("\n") if l.strip()]
    tool_lines = [l for l in lines if l.startswith("<<tool:") or l.startswith("<tool_call>")]
    non_tool_lines = [l for l in lines if not l.startswith("<<tool:") and not l.startswith("<tool_call>")]
    if len(tool_lines) > len(non_tool_lines):
        return 0.0
    if len(final) > 100:
        score += 0.3
    elif len(final) > 50:
        score += 0.1
    steps = trace.get("steps") or []
    if steps and all("ERROR" not in (s.get("content") or "") for s in steps):
        score += 0.2
    if len(steps) <= 4:
        score += 0.2
    low = final.lower()
    if "verdict:" in low and ("approve" in low or "reject" in low or "review" in low):
        score += 0.1
    return score


def _to_few_shot(trace: dict[str, Any]) -> dict[str, Any] | None:
    if trace.get("fallback") is True:
        return None
    final = (trace.get("final_text") or "").strip()
    if not final or len(final) < 80:
        return None
    low = final.lower()
    if low.startswith("<<tool:") or low.startswith("<tool_call>"):
        return None
    if "error" in low or "unavailable" in low:
        return None
    lines = [l.strip() for l in final.split("\n") if l.strip()]
    tool_lines = [l for l in lines if l.startswith("<<tool:") or l.startswith("<tool_call>")]
    non_tool_lines = [l for l in lines if not l.startswith("<<tool:") and not l.startswith("<tool_call>")]
    if len(tool_lines) > len(non_tool_lines):
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


def _score_buyer_verdict(verdict: Any) -> float:
    """Score buyer verdict quality."""
    if verdict.verdict not in ("accept", "decline"):
        return 0.0
    final = verdict.rationale.strip()
    if not final:
        return 0.0
    score = 0.3  # baseline
    if len(final) > 50:
        score += 0.3
    if "Verdict:" in final or "Verdict:" in final:
        score += 0.2
    if len(final) > 100:
        score += 0.2
    return score


def _to_buyer_few_shot(verdict: Any, scenario_name: str) -> dict[str, Any] | None:
    final = verdict.rationale.strip()
    if not final or len(final) < 40:
        return None
    if verdict.verdict not in ("accept", "decline"):
        return None
    return {
        "scenario": scenario_name,
        "turns": [
            {"role": "assistant", "content": final},
        ],
        "final_text": final,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="pretrain both merchant and buyer reasoners over live traces")
    parser.add_argument("--merchant-scenarios", type=int, default=DEFAULT_SCENARIOS)
    parser.add_argument("--buyer-scenarios", type=int, default=DEFAULT_SCENARIOS)
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--out", type=str, default=DEFAULT_OUT)
    parser.add_argument("--min-score", type=float, default=0.7)
    parser.add_argument("--verbose", action="store_true", default=False)
    args = parser.parse_args()

    store_path = ":memory:"

    # ── Merchant pretraining ─────────────────────────────────────────────────
    merchant_agent = _merchant_agent(store_path)
    merchant_raw: list[dict[str, Any]] = []

    merchant_pool = MERCHANT_SCENARIOS * max(1, args.merchant_scenarios // len(MERCHANT_SCENARIOS))
    for idx, scenario in enumerate(merchant_pool[: args.merchant_scenarios]):
        last_exc: BaseException | None = None
        for attempt in range(1, 4):
            try:
                result = merchant_agent.reason(
                    f"pretrain-merchant-{scenario['name']}",
                    target_sku=scenario["target_sku"],
                    item_category=scenario["item_category"],
                    cart_value_inr=scenario["cart_value_inr"],
                    buyer_allowance_inr=scenario["buyer_allowance_inr"],
                    is_stagnant=scenario.get("is_stagnant", False),
                    days_in_stock=scenario.get("days_in_stock"),
                    bandit_action=scenario.get("bandit_action"),
                    gate_decision=scenario.get("gate_decision"),
                )
                break
            except Exception as exc:
                last_exc = exc
                if attempt < 3:
                    time.sleep(2 ** attempt)
        else:
            result = ReasoningResult(
                session_id=f"pretrain-merchant-{scenario['name']}",
                provider="error",
                model="error",
                steps=[],
                final_text=f"Reasoning unavailable ({type(last_exc).__name__})",
                verdict="NONE",
                verdict_rationale="",
                fallback=True,
            )

        trace = {
            "session_id": result.session_id,
            "provider": result.provider,
            "fallback": result.fallback,
            "steps": [
                {"step": s.step, "role": s.role, "content": s.content}
                for s in result.steps
            ],
            "final_text": result.final_text,
            "scenario": scenario["name"],
        }
        merchant_raw.append(trace)

    # ── Buyer pretraining ────────────────────────────────────────────────────
    buyer_llm = _buyer_agent()
    buyer_raw: list[dict[str, Any]] = []

    buyer_pool = BUYER_SCENARIOS * max(1, args.buyer_scenarios // len(BUYER_SCENARIOS))
    for idx, scenario in enumerate(buyer_pool[: args.buyer_scenarios]):
        try:
            verdict = evaluate_offer(
                llm=buyer_llm,
                session=scenario["session"],
                cart_value_inr=scenario["cart_value_inr"],
                buyer_allowance_inr=scenario["buyer_allowance_inr"],
                memory=scenario["memory"],
                min_discount_percent=scenario["min_discount_percent"],
                max_add_on_share=scenario["max_add_on_share"],
            )
            trace = {
                "session_id": f"pretrain-buyer-{scenario['name']}",
                "verdict": verdict.verdict,
                "rationale": verdict.rationale,
                "offer": verdict.offer,
                "scenario": scenario["name"],
                "_score": _score_buyer_verdict(verdict),
            }
        except Exception as exc:
            trace = {
                "session_id": f"pretrain-buyer-{scenario['name']}",
                "verdict": "error",
                "rationale": f"Error: {type(exc).__name__}: {exc}",
                "offer": {},
                "scenario": scenario["name"],
                "_score": 0.0,
            }
        buyer_raw.append(trace)

    # ── Select and format examples ───────────────────────────────────────────
    merchant_scored = [(trace, _score_trace(trace)) for trace in merchant_raw]
    merchant_scored.sort(key=lambda pair: pair[1], reverse=True)
    merchant_selected: list[dict[str, Any]] = []
    for trace, score in merchant_scored:
        if score < args.min_score:
            continue
        few_shot = _to_few_shot(trace)
        if few_shot is not None:
            few_shot["agent"] = "merchant"
            merchant_selected.append(few_shot)

    buyer_selected: list[dict[str, Any]] = []
    for trace in buyer_raw:
        score = trace.get("_score", 0.0)
        if score < args.min_score:
            continue
        # Reconstruct verdict object
        from razorpay_agent.buyer.reasoning_agent import BuyerVerdict
        verdict = BuyerVerdict(verdict=trace["verdict"], rationale=trace["rationale"], offer=trace["offer"])
        few_shot = _to_buyer_few_shot(verdict, trace["scenario"])
        if few_shot is not None:
            few_shot["agent"] = "buyer"
            buyer_selected.append(few_shot)

    all_examples = merchant_selected + buyer_selected
    # Cap at 20 to keep prompt size reasonable
    if len(all_examples) > 20:
        # Keep at least 4 of each if available
        m_count = min(len(merchant_selected), max(4, 20 // 2))
        b_count = min(len(buyer_selected), 20 - m_count)
        all_examples = merchant_selected[:m_count] + buyer_selected[:b_count]

    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": buyer_llm.name,
        "provider": buyer_llm.name,
        "merchant_scenario_count": len(merchant_raw),
        "buyer_scenario_count": len(buyer_raw),
        "merchant_selected_count": len(merchant_selected),
        "buyer_selected_count": len(buyer_selected),
        "total_examples": len(all_examples),
        "min_score": args.min_score,
        "examples": all_examples,
    }

    Path(args.out).write_text(json.dumps(out, indent=2))
    print(
        f"[pretrain_reasoner] merchant={len(merchant_raw)}/{len(merchant_selected)} "
        f"buyer={len(buyer_raw)}/{len(buyer_selected)} "
        f"-> {args.out}"
    )


if __name__ == "__main__":
    main()
