"""Cart-based buyer agent with multi-step tool-calling reasoning.

The buyer agent:
1. Discovers products (curated by shop assistant or filtered by interests)
2. For each product, runs a multi-step reasoning loop:
   - Can call tools: get_catalog_item, get_budget_status, get_purchase_history, 
     get_interests, evaluate_offer_value, check_affordability, compute_total_with_items
   - After gathering info, outputs a structured verdict: ADD_TO_CART or SKIP
3. Checks out via merchant's payment link
4. Auto-pays (demo) or waits for webhook (real)

Uses LLMResponse for structured parsing — prevents hallucination by validating
tool calls against the registry and requiring explicit verdict format.
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

import httpx2 as httpx

from razorpay_agent.buyer.llm_response import LLMResponse, Verdict, strip_tool_calls
from razorpay_agent.buyer.tools import BuyerDeps, build_buyer_registry


@dataclass
class Personality:
    name: str = "Default Buyer"
    interests: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    budget_paise: int = 1_000_000
    max_single_purchase_paise: int = 500_000
    min_discount_percent: float = 5.0
    ideal_discount_percent: float = 15.0
    patience: int = 5
    impulsiveness: float = 0.3
    style: str = "analytical"
    demo_auto_pay: bool = True


@dataclass
class CartItem:
    product: dict
    quantity: int = 1


class CartBuyerAgent:
    """Buyer agent with multi-step tool-calling reasoning loop."""
    
    def __init__(
        self,
        base_url: str,
        personality: Personality,
        payment_token: str = "tok_ok",
        transport: Any = None,
        callback: Callable[[str], None] | None = None,
        llm: Any = None,
        curated_ids: list[str] | None = None,
        max_reasoning_steps: int = 4,
    ) -> None:
        self._base_url = base_url
        self._personality = personality
        self._payment_token = payment_token
        self._transport = transport
        self._callback = callback or (lambda msg: None)
        self._llm = llm
        self._max_reasoning_steps = max_reasoning_steps
        self._cart: list[CartItem] = []
        self._catalog: list[dict] = []
        self._curated_ids = curated_ids
        self._evaluated = 0
        self._running = False
        self._payment_link: dict | None = None
        self._order: dict | None = None
        self._purchase_history: list[dict] = []
        self._deps: BuyerDeps | None = None
    
    @property
    def personality(self) -> Personality:
        return self._personality
    
    @property
    def remaining_budget(self) -> int:
        spent = sum(ci.product["unit_amount"] * ci.quantity for ci in self._cart)
        return self._personality.budget_paise - spent
    
    @property
    def cart(self) -> list[CartItem]:
        return list(self._cart)
    
    @property
    def payment_link(self) -> dict | None:
        return self._payment_link
    
    @property
    def order(self) -> dict | None:
        return self._order
    
    def _note(self, msg: str) -> None:
        self._callback(msg)
    
    async def _get_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {"base_url": self._base_url, "timeout": 30.0}
        if self._transport:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)
    
    def _get_deps(self) -> BuyerDeps:
        if self._deps is None:
            self._deps = BuyerDeps(
                catalog=tuple(self._catalog),
                budget_paise=self._personality.budget_paise,
                spent_paise=sum(ci.product["unit_amount"] * ci.quantity for ci in self._cart),
                purchase_history=self._purchase_history,
                interests=self._personality.interests,
                avoid=self._personality.avoid,
                max_single_purchase_paise=self._personality.max_single_purchase_paise,
                min_discount_percent=self._personality.min_discount_percent,
            )
        return self._deps
    
    async def discover(self) -> list[dict]:
        """Discover products — uses curated IDs if available, else full catalog."""
        client = await self._get_client()
        resp = await client.get("/products")
        resp.raise_for_status()
        all_products = resp.json()["items"]
        await client.aclose()
        
        if self._curated_ids:
            curated = [p for p in all_products if p["id"] in self._curated_ids]
            if curated:
                self._catalog = curated
                self._note(f"📋 Shop assistant curated {len(curated)} products for you")
                return self._catalog
        
        self._catalog = all_products
        self._note(f"📋 Discovered {len(self._catalog)} products")
        return self._catalog
    
    def _filter_by_interests(self, items: list[dict]) -> list[dict]:
        if not self._personality.interests:
            return items
        filtered = [
            item for item in items
            if item["category"] in self._personality.interests
            or item["id"] in self._personality.interests
        ]
        return filtered if filtered else items
    
    def _should_consider(self, item: dict) -> tuple[bool, str]:
        if item["category"] in self._personality.avoid:
            return False, f"avoiding {item['category']}"
        if item["id"] in self._personality.avoid:
            return False, f"avoiding {item['id']}"
        if item["unit_amount"] > self.remaining_budget:
            return False, "over budget"
        if item["unit_amount"] > self._personality.max_single_purchase_paise:
            return False, "over single-purchase limit"
        return True, "within budget and interests"
    
    async def _resolve_llm(self) -> Any:
        """Resolve LLM — real LLM by default, fall back to stub on timeout/error."""
        if self._llm is not None:
            return self._llm
        
        # Try real LLM first (with generous timeout)
        try:
            import concurrent.futures

            from razorpay_agent.reasoning.llm import resolve_provider
            
            def _get_llm():
                llm = resolve_provider()
                # Quick test call to verify it works
                llm.complete("OK")
                return llm
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_get_llm)
                try:
                    llm = future.result(timeout=10.0)
                    if llm is not None:
                        return llm
                except (concurrent.futures.TimeoutError, Exception):
                    pass
        except Exception:
            pass
        
        # Fall back to stub
        from razorpay_agent.reasoning.llm import StubBackend
        self._llm = StubBackend()
        return self._llm
    
    async def _reason_about_item(self, item: dict) -> Verdict:
        """Multi-step reasoning loop: tools → verdict. All styles use LLM."""
        llm = await self._resolve_llm()
        registry = build_buyer_registry(self._get_deps())
        
        # Build system prompt with tool specs
        tool_specs = json.dumps(registry.specs(), indent=2)
        system = f"""You are a buyer agent with a {self._personality.style} personality shopping on General Goods Co.

YOUR TRAITS:
- Budget: ₹{self._personality.budget_paise/100:.0f} total
- Remaining: ₹{self.remaining_budget/100:.0f}
- Min discount: {self._personality.min_discount_percent}%
- Interests: {", ".join(self._personality.interests) if self._personality.interests else "anything"}
- Avoid: {", ".join(self._personality.avoid) if self._personality.avoid else "nothing"}

Available tools:
{tool_specs}

To call a tool, emit: <<tool:TOOL_NAME {{"arg": "value"}}>>

After 1-2 tool calls, give your final decision:
- A brief rationale (1-2 sentences)
- End with exactly: "Verdict: ADD_TO_CART" or "Verdict: SKIP"

Be concise. Use tools to gather info, then decide."""

        user = f"""Evaluate this product:

PRODUCT:
- Name: {item['title']} ({item['id']})
- Category: {item['category']}
- Price: ₹{item['unit_amount']/100:.0f}
- Description: {item.get('description', 'No description available')}
- Rating: {item.get('rating', 'N/A')} ({item.get('reviews', 0)} reviews)
- Tags: {', '.join(item.get('tags', []))}
- Stock: {item.get('stock', 'unknown')} units remaining

Use tools to check your budget and purchase history, then decide: ADD_TO_CART or SKIP."""
        
        history = [("system", system), ("user", user)]
        steps = []
        
        for step_num in range(self._max_reasoning_steps):
            prompt = _render_history(history)
            force_final = step_num == self._max_reasoning_steps - 1
            
            if force_final:
                prompt += "\n\nFINAL ANSWER REQUIRED: Give your verdict now. End with 'Verdict: ADD_TO_CART' or 'Verdict: SKIP'."
            
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(llm.complete, prompt),
                    timeout=30.0
                )
            except asyncio.TimeoutError:
                self._note("  ❌ LLM timeout — skipping item")
                return Verdict(decision="SKIP", rationale="LLM timeout", confidence=0.0)
            
            response = LLMResponse.parse(text)
            
            if response.tool_call is not None and not force_final:
                # Execute tool
                result = registry.call(response.tool_call.name, response.tool_call.args)
                self._note(f"  🔧 {response.tool_call.name}({response.tool_call.args}) → {result[:80]}")
                
                # Add to history
                tool_text = strip_tool_calls(text)
                if tool_text:
                    history.append(("assistant", tool_text))
                history.append(("user", f"TOOL_RESULT: {result}"))
                steps.append(response)
                continue
            
            # Final verdict
            if response.verdict is not None:
                return response.verdict
            
            # No clear verdict — continue loop
            remaining = strip_tool_calls(text)
            if remaining:
                history.append(("assistant", remaining))
            history.append(("user", "Please give your final verdict: ADD_TO_CART or SKIP"))
        
        # Exhausted steps without verdict
        return Verdict(decision="SKIP", rationale="Could not reach verdict", confidence=0.3)
    
    async def _browse_and_build_cart(self) -> None:
        self._note(f"🛒 Shopping as '{self._personality.name}'")
        self._note(f"   Budget: ₹{self._personality.budget_paise/100:.0f} | Style: {self._personality.style}")
        
        await self.discover()
        candidates = self._filter_by_interests(self._catalog)
        random.shuffle(candidates)
        
        for item in candidates:
            if not self._running:
                break
            if self._evaluated >= self._personality.patience:
                self._note("⏹️ Reached patience limit")
                break
            if self.remaining_budget <= 0:
                self._note("💰 Budget exhausted")
                break
            
            should, reason = self._should_consider(item)
            if not should:
                self._note(f"⏭️ Skipping {item['id']}: {reason}")
                continue
            
            self._evaluated += 1
            verdict = await self._reason_about_item(item)
            
            self._note(f"  💭 {verdict.rationale[:150]}")
            
            if verdict.decision == "ADD_TO_CART":
                self._cart.append(CartItem(product=item, quantity=1))
                self._note(f"  🛍️ Added {item['title']} to cart")
                self._purchase_history.append({
                    "item_id": item["id"],
                    "category": item["category"],
                    "price_paise": item["unit_amount"],
                })
            else:
                self._note(f"  ❌ Skipping {item['title']}")
    
    async def _checkout(self) -> dict | None:
        """Checkout: create session, get payment link."""
        if not self._cart:
            self._note("🛒 Cart is empty")
            return None
        
        cart_titles = [ci.product["title"] for ci in self._cart]
        self._note(f"🛒 Checking out {len(self._cart)} items: {', '.join(cart_titles)}")
        
        client = await self._get_client()
        now = datetime.now(UTC)
        
        session_items = [{"id": ci.product["id"], "quantity": ci.quantity} for ci in self._cart]
        total = sum(ci.product["unit_amount"] for ci in self._cart)
        
        body = {
            "items": session_items,
            "allowance": {
                "reason": "one_time",
                "max_amount": min(total, self._personality.max_single_purchase_paise),
                "currency": "inr",
                "expires_at": (now + timedelta(minutes=30)).isoformat(),
            },
        }
        
        resp = await client.post("/checkout_sessions", json=body)
        resp.raise_for_status()
        session = resp.json()
        
        if session["status"] != "ready_for_payment":
            reason = session.get("messages", [{}])[0].get("content", "unknown")
            self._note(f"  ⚠️ Session not ready: {reason}")
            return None
        
        session_id = session["id"]
        self._note(f"📦 Session created: {session_id}")
        
        resp = await client.post(f"/checkout_sessions/{session_id}/create-payment-link")
        if resp.status_code != 200:
            self._note("  ❌ Failed to create payment link")
            return None
        
        link_data = resp.json()
        self._payment_link = link_data
        
        if self._personality.demo_auto_pay:
            self._note("🤖 Auto-paying via test API — no action needed")
        else:
            # Prefer razorpay_url for modal, fallback to internal checkout
            url = link_data.get('razorpay_url') or link_data.get('url', '')
            amount_paise = link_data.get('amount_paise', 0)
            if amount_paise > 0:
                self._note(f"🔗 Payment link: {url} (₹{amount_paise / 100:.0f})")
            else:
                self._note(f"🔗 Payment link: {url}")
        
        if 'session_id' not in self._payment_link:
            self._payment_link['session_id'] = session_id
        
        await client.aclose()
        return self._payment_link
    
    async def _wait_for_payment(self, timeout: int = 120) -> bool:
        """Wait for payment to complete."""
        if not self._payment_link:
            return False
        
        self._note("⏳ Processing payment...")
        
        if self._personality.demo_auto_pay:
            self._note("🤖 Demo mode: completing payment via test API...")
            await asyncio.sleep(0.3)
            try:
                client = await self._get_client()
                resp = await client.post("/api/simulate-payment", json={
                    "link_id": self._payment_link["id"],
                    "session_id": self._payment_link.get("session_id", ""),
                })
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("paid"):
                        self._order = data
                        self._note("✅ Payment completed!")
                        return True
            except Exception as e:
                self._note(f"Payment error: {e}")
            return False
        
        # Real mode: wait for webhook
        try:
            from razorpay_agent.checkout.api import _register_payment_event
            session_id = self._payment_link.get("session_id")
            if session_id:
                event = _register_payment_event(session_id)
                try:
                    await asyncio.wait_for(event.wait(), timeout=timeout)
                    client = await self._get_client()
                    resp = await client.get(f"/api/webhook/status/{session_id}")
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("status") == "completed":
                            self._order = data
                            self._note("✅ Payment successful!")
                            return True
                except asyncio.TimeoutError:
                    pass
        except ImportError:
            pass
        
        self._note("❌ Payment timed out")
        return False
    
    async def run(self) -> dict:
        """Run full flow: browse → cart → payment link → wait for payment."""
        self._running = True
        self._evaluated = 0
        
        await self._browse_and_build_cart()
        
        if not self._cart:
            self._note("🏁 No items in cart, done")
            self._running = False
            return {"status": "empty_cart", "cart": []}
        
        payment_link = await self._checkout()
        
        if not payment_link:
            self._note("🏁 Checkout failed")
            self._running = False
            return {"status": "checkout_failed", "cart": [ci.product["id"] for ci in self._cart]}
        
        paid = await self._wait_for_payment()
        
        self._running = False
        
        if paid:
            self._note(f"🏁 Complete! Order: {self._order}")
            return {
                "status": "completed",
                "order": self._order,
                "cart": [ci.product["id"] for ci in self._cart],
                "payment_link": self._payment_link,
            }
        else:
            self._note("🏁 Payment not received")
            return {
                "status": "payment_pending",
                "cart": [ci.product["id"] for ci in self._cart],
                "payment_link": self._payment_link,
            }
    
    def stop(self) -> None:
        self._running = False


def _render_history(history: list[tuple[str, str]]) -> str:
    out = []
    for role, content in history:
        tag = {"system": "SYSTEM", "user": "USER", "assistant": "ASSISTANT"}.get(role, role.upper())
        out.append(f"[{tag}]\n{content}")
    return "\n\n".join(out)
