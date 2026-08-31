from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from razorpay_agent.checkout.catalog import find_product
from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.gate.gate import RulePolicyGateConfig


@dataclass
class ReasoningDeps:
    """Read-only context the reasoning tools may consult. No settlement, no writes."""

    catalog: tuple[Any, ...]
    policy: Any | None
    gate_config: RulePolicyGateConfig
    regimen_graph: Any | None = None


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[[dict, ReasoningDeps], str]
    args_schema: dict[str, str] | None = None
    check_fn: Callable[[ReasoningDeps], bool] | None = None

    def available(self, deps: ReasoningDeps) -> bool:
        return self.check_fn is None or self.check_fn(deps)

    def run(self, args: dict, deps: ReasoningDeps) -> str:
        try:
            return self.fn(args, deps)
        except Exception as exc:  # error wrapping: never raise into the agent loop
            return f"ERROR[{type(exc).__name__}]: {exc}"


_REGISTERED: list[Tool] = []


def register_tool(
    name: str,
    description: str,
    args_schema: dict[str, str] | None = None,
    check_fn: Callable[[ReasoningDeps], bool] | None = None,
):
    def decorator(fn: Callable[[dict, ReasoningDeps], str]) -> Callable[[dict, ReasoningDeps], str]:
        _REGISTERED.append(Tool(name, description, fn, args_schema, check_fn))
        return fn

    return decorator


class ToolRegistry:
    """Self-registering, read-only tool registry for the reasoner.

    Each tool is available only when its ``check_fn`` passes (e.g. the bandit must
    exist to read its scores). Calls are error-wrapped so a failing tool returns a
    string rather than aborting the reasoning loop.
    """

    def __init__(self, deps: ReasoningDeps) -> None:
        self._deps = deps
        self._tools: dict[str, Tool] = {t.name: t for t in _REGISTERED}

    def specs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in self._tools.values():
            spec: dict[str, Any] = {"name": t.name, "description": t.description}
            if t.args_schema:
                spec["args"] = t.args_schema
            out.append(spec)
        return out

    def call(self, name: str, args: dict | None = None) -> str:
        args = args or {}
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR[UnknownTool]: no tool named {name!r}"
        if not tool.available(self._deps):
            return f"ERROR[ToolUnavailable]: {name} is not available in this context"
        return tool.run(args, self._deps)


@register_tool(
    "get_catalog_item",
    "Return a catalog item's title, category, and price by sku.",
    args_schema={"sku": "SKU of the catalog item"},
)
def _get_catalog_item(args: dict, deps: ReasoningDeps) -> str:
    sku = args["sku"]
    product = find_product(deps.catalog, sku)
    if product is None:
        raise KeyError(f"unknown sku {sku!r}")
    return json.dumps(
        {
            "id": product.id,
            "title": product.title,
            "category": product.category,
            "unit_amount_paise": product.unit_amount_paise,
            "stagnant": product.stagnant,
            "days_in_stock": product.days_in_stock,
        }
    )


@register_tool(
    "get_clearance_policy",
    "Return the merchant's clearance discount limits (max %, rupee cap) from the gate config.",
    args_schema={},
)
def _get_clearance_policy(args: dict, deps: ReasoningDeps) -> str:
    cfg = deps.gate_config
    return json.dumps(
        {
            "max_discount_percent": cfg.max_discount_percent,
            "max_discount_rupee_cap": cfg.max_discount_rupee_cap,
            "clearance_max_discount_percent": cfg.clearance_max_discount_percent,
            "clearance_max_discount_rupee_cap": cfg.clearance_max_discount_rupee_cap,
            "max_bundle_cart_share": cfg.max_bundle_cart_share,
        }
    )


@register_tool(
    "get_bandit_scores",
    "Return the LinUCB bandit's current preference scores across arms for a decision context.",
    args_schema={
        "target_sku": "the SKU the offer targets",
        "item_category": "category of the target SKU (e.g. apparel)",
        "cart_value_inr": "current cart value in INR",
        "buyer_allowance_inr": "buyer-agent spending allowance in INR",
        "is_stagnant": "true if this is a stagnant-clearance session",
        "days_in_stock": "days in stock (required when is_stagnant is true)",
    },
    check_fn=lambda deps: deps.policy is not None,
)
def _get_bandit_scores(args: dict, deps: ReasoningDeps) -> str:
    context = DecisionContext(
        session_id=str(args.get("session_id", "reasoning")),
        target_sku=str(args["target_sku"]),
        item_category=str(args["item_category"]),
        cart_value_inr=float(args["cart_value_inr"]),
        buyer_allowance_inr=float(args["buyer_allowance_inr"]),
        is_stagnant=bool(args.get("is_stagnant", False)),
        days_in_stock=args.get("days_in_stock"),
    )
    return json.dumps(deps.policy.scores(context))


@register_tool(
    "estimate_outcome",
    "Estimate completion probability and expected net revenue for an offer in a session.",
    args_schema={
        "offer": "offer dict with action_type and relevant fields",
        "session": "session dict with cart_value_inr, is_stagnant, etc.",
    },
)
def _estimate_outcome(args: dict, deps: ReasoningDeps) -> str:
    offer = args["offer"]
    session = args["session"]
    cart = float(session["cart_value_inr"])
    is_stagnant = bool(session.get("is_stagnant", False))
    action_type = offer.get("action_type")
    if action_type == "discount":
        pct = float(offer.get("discount_percent", 0.0))
        prob = min(0.95, 0.5 + (pct / 100.0) * 0.5)
    elif action_type == "bundle_upsell":
        prob = 0.6
    else:
        prob = 0.5
    expected = cart * prob
    return json.dumps(
        {
            "completion_probability": round(prob, 3),
            "expected_net_revenue_inr": round(expected, 2),
            "is_stagnant": is_stagnant,
        }
    )


@register_tool(
    "get_regimen_graph",
    "Return co-purchase / regimen neighbours for a target sku from the merchant graph.",
    args_schema={"target_sku": "the SKU to look up regimen neighbours for"},
    check_fn=lambda deps: deps.regimen_graph is not None,
)
def _get_regimen_graph(args: dict, deps: ReasoningDeps) -> str:
    sku = args["target_sku"]
    neighbors = deps.regimen_graph.neighbors(sku)
    return json.dumps(
        [
            {"target": e.target, "weight": e.weight, "relation": e.relation}
            for e in neighbors
        ]
    )


def build_registry(deps: ReasoningDeps) -> ToolRegistry:
    return ToolRegistry(deps)
