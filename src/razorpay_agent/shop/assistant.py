"""Shop assistant merchant reasoner — curates products by buyer interest.

When a buyer enters the shop and declares interests, the merchant's shop
assistant reasoner:
1. Greets the buyer
2. Curates a personalized selection from the catalog
3. Suggests complementary items (cross-sell via regimen graph)
4. May offer an opening discount / incentive
5. Surfaces stock urgency for stagnant items
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from razorpay_agent.checkout.catalog import Product, find_product
from razorpay_agent.reasoning.llm import LLMBackend, resolve_provider
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph


@dataclass
class ShopRecommendation:
    """A single product recommendation from the shop assistant."""
    product: Product
    reason: str  # why this product was recommended
    is_complement: bool = False  # cross-sell from regimen graph
    urgency_note: str | None = None  # for stagnant/clearance items


@dataclass
class ShopGreeting:
    """Full shop assistant response."""
    greeting: str
    reasoning: str
    recommendations: list[ShopRecommendation]
    opening_offer: str | None = None


# System prompt for the shop assistant reasoner
SHOP_SYSTEM_TEMPLATE = """You are a knowledgeable, friendly shop assistant at General Goods Co.
Your job is to help a buyer discover products that match their interests.

KEY RULES:
- Match products to the buyer's stated interests and budget
- Highlight variety across categories when interests are broad
- Call out clearance/stagnant items only if they genuinely match interests
- Suggest complementary items when natural (e.g., shaker + coffee, mug + candle)
- Keep recommendations focused — 6-8 products max, quality over quantity
- Prioritize products that fit well within budget (don't blow it all on one item)
- Include at least one cross-sell/complementary item from a different category

AVAILABLE TOOLS:
{tool_specs}

To call a tool, emit: <<tool:tool_name {{"arg": "value"}}>>

After 1-2 tool calls, give your recommendation as JSON:
{{
  "greeting": "warm, personalized greeting mentioning the buyer's interests",
  "reasoning": "brief explanation of selection strategy (variety, value, cross-sell)",
  "recommendations": [
    {{
      "sku": "sku-xxx",
      "reason": "specific reason this matches (mention category, price point, or complement)",
      "is_complement": false,
      "urgency_note": null
    }}
  ],
  "opening_offer": "optional incentive like 'Free shipping on orders above ₹2000!', or null"
}}

IMPORTANT: Include 6-8 products. At least 2 should be cross-sells from different categories.
Keep it concise. Buyer will then browse and decide what to buy."""


@dataclass
class _Tool:
    name: str
    description: str
    fn: callable
    args_schema: dict[str, str] | None = None


class _ShopToolRegistry:
    def __init__(self, catalog: tuple[Product, ...], regimen_graph: CoPurchaseGraph):
        self._catalog = catalog
        self._regimen = regimen_graph

    def specs(self) -> list[dict]:
        return [
            {"name": "get_products_by_interest",
             "description": "Filter products by category or keyword",
             "args": {"interest": "category name or keyword"}},
            {"name": "get_regimen_neighbors",
             "description": "Get complementary products for a given SKU",
             "args": {"sku": "SKU to find complements for"}},
        ]

    def call(self, name: str, args: dict) -> str:
        if name == "get_products_by_interest":
            interest = args["interest"].lower()
            matches = [
                p for p in self._catalog
                if interest in p.category.lower()
                or interest in p.title.lower()
                or interest in p.id.lower()
            ]
            return json.dumps([{"id": p.id, "title": p.title, "category": p.category,
                                "price_paise": p.unit_amount_paise,
                                "stagnant": p.stagnant,
                                "days_in_stock": p.days_in_stock}
                               for p in matches])
        elif name == "get_regimen_neighbors":
            sku = args["sku"]
            neighbors = self._regimen.neighbors(sku)
            return json.dumps([{"target": e.target, "weight": e.weight,
                                "relation": e.relation} for e in neighbors])
        return f"ERROR: unknown tool {name}"


# Reuse the merchant reasoner's tool-call extraction
_TOOL_CALL_RE = re.compile(
    r"<<tool:(\w+)\s*(\{.*?\})?>>|<tool_call>(\w+)(?:\s*(\{.*?\}))?\s*(?:</tool_call>|$|<tool_call>)",
    re.DOTALL,
)


def _try_extract_tool(text: str) -> tuple[str, dict] | None:
    match = _TOOL_CALL_RE.search(text)
    if match:
        if match.group(1):
            name = match.group(1)
            args_json = match.group(2) or "{}"
        else:
            name = match.group(3)
            args_json = match.group(4) or "{}"
        try:
            return name, json.loads(args_json) if args_json.strip() else {}
        except json.JSONDecodeError:
            return name, {}
    return None


class ShopAssistantAgent:
    """Shop assistant — greets buyer, recommends products, creates curated experience."""

    def __init__(
        self,
        catalog: tuple[Product, ...],
        regimen_graph: CoPurchaseGraph,
        llm: LLMBackend | None = None,
        max_steps: int = 4,
    ):
        self._catalog = catalog
        self._regimen = regimen_graph
        self._llm = llm or resolve_provider()
        self._max_steps = max_steps
        self._tools = _ShopToolRegistry(catalog, regimen_graph)

    def greet_and_recommend(
        self,
        interests: list[str],
        budget_paise: int,
        style: str = "analytical",
    ) -> ShopGreeting:
        """Greet the buyer and curate product recommendations."""
        tool_specs = json.dumps(self._tools.specs(), indent=2)
        system = SHOP_SYSTEM_TEMPLATE.format(tool_specs=tool_specs)

        interest_str = ", ".join(interests) if interests else "anything interesting"
        budget_rupees = budget_paise / 100
        user = f"""A buyer has entered the shop.
Interests: {interest_str}
Budget: ₹{budget_rupees:.0f}
Shopping style: {style}

Recommend 4-6 products that match their interests and budget.
Use tools to explore the catalog if needed, then respond with the JSON structure."""

        history = [("system", system), ("user", user)]
        tool_calls = 0
        greeting = None

        for step_num in range(self._max_steps * 2):  # Give more steps for better curation
            prompt = "\n\n".join(
                f"[{role.upper()}]\n{content}" for role, content in history
            )
            force_final = tool_calls >= 2 or step_num == self._max_steps - 1
            if force_final:
                prompt += "\n\nFINAL ANSWER REQUIRED: respond with the JSON structure now."

            text = self._llm.complete(prompt)
            tool_call = _try_extract_tool(text)

            if tool_call is not None and tool_calls < 2 and not force_final:
                name, args = tool_call
                result = self._tools.call(name, args)
                history.append(("assistant", f"<<tool:{name} {json.dumps(args)}>>"))
                history.append(("user", f"TOOL_RESULT: {result}"))
                tool_calls += 1
                continue

            # Try to extract JSON from the response
            try:
                # Try to find JSON in the response
                json_match = re.search(r'\{[\s\S]*\}', text)
                if json_match:
                    data = json.loads(json_match.group())
                    greeting = self._parse_greeting(data)
                    if greeting.recommendations:
                        return greeting
            except (json.JSONDecodeError, KeyError):
                pass

            # If we got text but no JSON, try to use it as a fallback
            if step_num == self._max_steps - 1 or force_final:
                break
        
        # Fallback: programmatic recommendation if LLM didn't produce valid JSON or empty recs
        fallback = self._fallback_recommendation(interests, budget_paise, style)
        if greeting and greeting.recommendations:
            # Merge: use LLM greeting text but ensure we have recommendations
            if not greeting.greeting or greeting.greeting == "Welcome to General Goods Co.!":
                greeting.greeting = fallback.greeting
            if not greeting.reasoning or greeting.reasoning == "Here are some products you might like.":
                greeting.reasoning = fallback.reasoning
            # If LLM gave no recs but fallback did, use fallback recs
            if not greeting.recommendations:
                greeting.recommendations = fallback.recommendations
            return greeting
        # LLM gave nothing useful — use fallback
        return fallback

    def _parse_greeting(self, data: dict) -> ShopGreeting:
        """Parse the LLM's JSON response into a ShopGreeting."""
        recs = []
        for r in data.get("recommendations", []):
            product = find_product(self._catalog, r.get("sku", ""))
            if product:
                recs.append(ShopRecommendation(
                    product=product,
                    reason=r.get("reason", "matches your interests"),
                    is_complement=r.get("is_complement", False),
                    urgency_note=r.get("urgency_note"),
                ))
        return ShopGreeting(
            greeting=data.get("greeting", "Welcome to General Goods Co.!"),
            reasoning=data.get("reasoning", "Here are some products you might like."),
            recommendations=recs,
            opening_offer=data.get("opening_offer"),
        )

    def _needs_fallback(self, greeting: ShopGreeting) -> bool:
        """Check if the greeting needs fallback recommendations."""
        return len(greeting.recommendations) < 3

    def _fallback_recommendation(
        self, interests: list[str], budget_paise: int, style: str
    ) -> ShopGreeting:
        """Programmatic fallback when LLM doesn't produce valid JSON.
        
        Uses co-purchase graph for cross-sell and ensures variety across categories.
        """
        recs = []
        seen = set()
        
        # 1. Match by interest categories (primary matches)
        for interest in interests:
            for p in self._catalog:
                if p.id in seen:
                    continue
                if (interest.lower() in p.category.lower()
                        or interest.lower() in p.title.lower()):
                    if p.unit_amount_paise <= budget_paise:
                        recs.append(ShopRecommendation(
                            product=p,
                            reason=f"Matches your interest in {interest}",
                        ))
                        seen.add(p.id)
        
        # 2. Add cross-sell items from co-purchase graph
        for rec in list(recs):
            neighbors = self._regimen.neighbors(rec.product.id)
            for edge in neighbors:
                neighbor_product = find_product(self._catalog, edge.target)
                if neighbor_product and neighbor_product.id not in seen:
                    if neighbor_product.unit_amount_paise <= budget_paise:
                        recs.append(ShopRecommendation(
                            product=neighbor_product,
                            reason=f"Complements {rec.product.title} ({edge.relation.replace('_', ' ')})",
                            is_complement=True,
                        ))
                        seen.add(neighbor_product.id)
        
        # 3. Add stagnant/clearance items for deal hunters
        if style in ("aggressive", "analytical"):
            for p in self._catalog:
                if p.id in seen:
                    continue
                if p.stagnant and p.unit_amount_paise <= budget_paise:
                    recs.append(ShopRecommendation(
                        product=p,
                        reason=f"Clearance: {p.days_in_stock} days in stock — great deal",
                        urgency_note=f"Only {p.days_in_stock} days left at this price",
                    ))
                    seen.add(p.id)
        
        # 4. Fill remaining slots with affordable variety
        if len(recs) < 6:
            for p in sorted(self._catalog, key=lambda x: x.unit_amount_paise):
                if p.id in seen:
                    continue
                if p.unit_amount_paise <= budget_paise:
                    recs.append(ShopRecommendation(
                        product=p,
                        reason=f"Popular {p.category} item within budget",
                    ))
                    seen.add(p.id)
                    if len(recs) >= 8:
                        break
        
        # Build style-appropriate greeting
        style_greetings = {
            "analytical": f"Welcome! I've curated {len(recs)} products matching your interests with price-performance analysis.",
            "impulsive": f"Hey! Check out these {len(recs)} amazing finds we picked just for you!",
            "cautious": f"Welcome! Here are {len(recs)} carefully selected items that match your needs.",
            "aggressive": f"Welcome! I found {len(recs)} deals for you — including clearance bargains.",
        }
        greeting = style_greetings.get(style, f"Welcome! I've curated {len(recs)} products for you.")
        
        return ShopGreeting(
            greeting=greeting,
            reasoning=f"Selected {len(recs)} products across {len({r.product.category for r in recs})} categories matching: {', '.join(interests)}",
            recommendations=recs,
            opening_offer="Free shipping on orders above ₹2000!" if len(recs) >= 3 else None,
        )
