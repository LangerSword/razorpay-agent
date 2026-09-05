"""Buy-side tool registry for the CartBuyerAgent.

Multi-step, multi-tool harness: the buyer can inspect the catalog, check its
budget, review purchase history, and evaluate offers through registered read-only
tools — same architecture as the merchant reasoner but oriented toward
evaluation and decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class BuyerDeps:
    """Read-only context the buyer tools may consult."""
    catalog: tuple[Any, ...]
    budget_paise: int
    spent_paise: int = 0
    purchase_history: list[dict] | None = None
    interests: list[str] | None = None
    avoid: list[str] | None = None
    max_single_purchase_paise: int = 500_000
    min_discount_percent: float = 5.0
    max_add_on_share: float = 0.25

    @property
    def remaining_budget(self) -> int:
        return self.budget_paise - self.spent_paise


@dataclass
class BuyerTool:
    name: str
    description: str
    fn: Callable[[dict, BuyerDeps], str]
    args_schema: dict[str, str] | None = None

    def run(self, args: dict, deps: BuyerDeps) -> str:
        try:
            return self.fn(args, deps)
        except Exception as exc:
            return f"ERROR[{type(exc).__name__}]: {exc}"


_REGISTERED: list[BuyerTool] = []


def register_buyer_tool(
    name: str,
    description: str,
    args_schema: dict[str, str] | None = None,
):
    def decorator(fn: Callable[[dict, BuyerDeps], str]) -> Callable[[dict, BuyerDeps], str]:
        _REGISTERED.append(BuyerTool(name, description, fn, args_schema))
        return fn
    return decorator


class BuyerToolRegistry:
    def __init__(self, deps: BuyerDeps) -> None:
        self._deps = deps
        self._tools: dict[str, BuyerTool] = {t.name: t for t in _REGISTERED}

    def specs(self) -> list[dict[str, Any]]:
        out = []
        for t in self._tools.values():
            spec = {"name": t.name, "description": t.description}
            if t.args_schema:
                spec["args"] = t.args_schema
            out.append(spec)
        return out

    def call(self, name: str, args: dict | None = None) -> str:
        args = args or {}
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR[UnknownTool]: no tool named {name!r}"
        return tool.run(args, self._deps)


@register_buyer_tool(
    "get_catalog_item",
    "Return a catalog item's title, category, price, and image by sku.",
    args_schema={"sku": "SKU of the catalog item"},
)
def _get_catalog_item(args: dict, deps: BuyerDeps) -> str:
    from razorpay_agent.checkout.catalog import find_product
    sku = args["sku"]
    product = find_product(deps.catalog, sku)
    if product is None:
        raise KeyError(f"unknown sku {sku!r}")
    return json.dumps({
        "id": product.id,
        "title": product.title,
        "category": product.category,
        "unit_amount_paise": product.unit_amount_paise,
        "image_url": product.image_url,
    })


@register_buyer_tool(
    "get_budget_status",
    "Return remaining budget and spending status.",
)
def _get_budget_status(args: dict, deps: BuyerDeps) -> str:
    return json.dumps({
        "budget_paise": deps.budget_paise,
        "spent_paise": deps.spent_paise,
        "remaining_paise": deps.remaining_budget,
        "max_single_purchase_paise": deps.max_single_purchase_paise,
    })


@register_buyer_tool(
    "get_purchase_history",
    "Return recent purchase history (what the buyer already owns).",
)
def _get_purchase_history(args: dict, deps: BuyerDeps) -> str:
    history = deps.purchase_history or []
    return json.dumps({
        "purchase_count": len(history),
        "recent_purchases": [
            {"item_id": p.get("item_id"), "category": p.get("category"), "price_paise": p.get("price_paise")}
            for p in history[-5:]
        ],
    })


@register_buyer_tool(
    "get_interests",
    "Return the buyer's current interests and avoid list.",
)
def _get_interests(args: dict, deps: BuyerDeps) -> str:
    return json.dumps({
        "interests": deps.interests or [],
        "avoid": deps.avoid or [],
        "min_discount_percent": deps.min_discount_percent,
        "max_add_on_share": deps.max_add_on_share,
    })


@register_buyer_tool(
    "evaluate_offer_value",
    "Compute whether an offer is proportionate given budget, cart, and buyer criteria.",
    args_schema={
        "offer_type": "discount or bundle_upsell",
        "discount_percent": "discount percentage (for discount offers)",
        "add_on_price_paise": "add-on price in paise (for bundles)",
        "cart_value_paise": "current cart value in paise",
    },
)
def _evaluate_offer_value(args: dict, deps: BuyerDeps) -> str:
    offer_type = args.get("offer_type")
    cart_value = args.get("cart_value_paise", 0)

    if offer_type == "discount":
        disc_pct = args.get("discount_percent", 0)
        meets_threshold = disc_pct >= deps.min_discount_percent
        return json.dumps({
            "offer_type": "discount",
            "discount_percent": disc_pct,
            "meets_min_threshold": meets_threshold,
            "min_required": deps.min_discount_percent,
            "assessment": "good" if meets_threshold else "below_threshold",
        })
    elif offer_type == "bundle_upsell":
        add_on_price = args.get("add_on_price_paise", 0)
        share = add_on_price / cart_value if cart_value > 0 else 1.0
        within_share = share <= deps.max_add_on_share
        return json.dumps({
            "offer_type": "bundle",
            "add_on_price_paise": add_on_price,
            "cart_share": round(share, 4),
            "max_share": deps.max_add_on_share,
            "within_limit": within_share,
            "assessment": "proportionate" if within_share else "too_expensive",
        })
    return json.dumps({"offer_type": "none", "assessment": "no_offer"})


@register_buyer_tool(
    "check_affordability",
    "Check if a purchase fits within budget and single-purchase limits.",
    args_schema={"price_paise": "price to check"},
)
def _check_affordability(args: dict, deps: BuyerDeps) -> str:
    price = args.get("price_paise", 0)
    fits_budget = price <= deps.remaining_budget
    fits_single = price <= deps.max_single_purchase_paise
    return json.dumps({
        "price_paise": price,
        "fits_budget": fits_budget,
        "fits_single_purchase_limit": fits_single,
        "remaining_paise": deps.remaining_budget,
        "can_afford": fits_budget and fits_single,
    })


@register_buyer_tool(
    "compute_total_with_items",
    "Compute total cart value if specific items were added.",
    args_schema={
        "current_total_paise": "current cart total",
        "new_items_paise": "list of new item prices",
    },
)
def _compute_total_with_items(args: dict, deps: BuyerDeps) -> str:
    current = args.get("current_total_paise", 0)
    new_items = args.get("new_items_paise", [])
    new_total = current + sum(new_items)
    return json.dumps({
        "current_total_paise": current,
        "new_items_paise": new_items,
        "projected_total_paise": new_total,
        "remaining_after_purchase": deps.budget_paise - new_total,
    })


def build_buyer_registry(deps: BuyerDeps) -> BuyerToolRegistry:
    return BuyerToolRegistry(deps)
