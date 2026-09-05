from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

from razorpay_agent.buyer.reasoning_agent import (
    ACCEPT,
    DECLINE,
    PurchaseMemory,
    PurchaseRecord,
    _effective_discount_percent,
    _total_of,
    evaluate_offer,
)


@dataclass(frozen=True)
class PurchaseResult:
    final_status: str
    accepted_offer: bool
    order: dict | None
    transcript: tuple[str, ...] = field(default_factory=tuple)


class BuyerAgent:
    """ACP-speaking buyer agent with its own reasoning loop and purchase memory.

    The buyer discovers the catalog, creates sessions, receives offers, and
    decides whether to accept or decline — using an LLM reasoner that references
    its purchase memory. Every decision is legible via a transcript."""

    def __init__(
        self,
        base_url: str,
        max_allowance_paise: int = 10_000_000,
        payment_token: str = "tok_ok",
        allowance_ttl_minutes: int = 30,
        min_worthwhile_discount_percent: float = 5.0,
        max_add_on_cart_share: float = 0.25,
        transport: Any | None = None,
        timeout: float = 30.0,
        memory: PurchaseMemory | None = None,
        llm: Any | None = None,
    ) -> None:
        self._max_allowance_paise = max_allowance_paise
        self._payment_token = payment_token
        self._ttl_minutes = allowance_ttl_minutes
        self._min_discount_percent = min_worthwhile_discount_percent
        self._max_add_on_share = max_add_on_cart_share
        self._client = httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=httpx.Timeout(timeout, connect=5.0)
        )
        self.transcript: list[str] = []
        self.last_session: dict | None = None
        self._memory = memory or PurchaseMemory()
        if llm is None:
            from razorpay_agent.reasoning.llm import resolve_provider
            llm = resolve_provider()
        self._llm = llm

    @property
    def memory(self) -> PurchaseMemory:
        return self._memory

    async def discover(self) -> list[dict[str, Any]]:
        response = await self._client.get("/products")
        response.raise_for_status()
        items = response.json()["items"]
        self._note(f"discovered {len(items)} products from the merchant feed")
        return items

    async def start_session(self, item_id: str, quantity: int = 1) -> dict[str, Any]:
        now = datetime.now(UTC)
        body = {
            "items": [{"id": item_id, "quantity": quantity}],
            "allowance": {
                "reason": "one_time",
                "max_amount": self._max_allowance_paise,
                "currency": "inr",
                "expires_at": (now + timedelta(minutes=self._ttl_minutes)).isoformat(),
            },
        }
        response = await self._client.post("/checkout_sessions", json=body)
        response.raise_for_status()
        session = response.json()
        self.last_session = session
        self._note(
            f"created checkout session {session['id']} for {item_id} x{quantity}; "
            f"presented scoped mandate of {self._max_allowance_paise} paise"
        )
        return session

    def review_offer(self, session: dict[str, Any]) -> tuple[str, Any]:
        """Evaluate the offer using the LLM reasoner.

        Returns (decision, verdict) where decision is ACCEPT/DECLINE.
        """
        cart_value_inr = _total_of(session) / 100.0
        buyer_allowance_inr = self._max_allowance_paise / 100.0

        verdict = evaluate_offer(
            llm=self._llm,
            session=session,
            cart_value_inr=cart_value_inr,
            buyer_allowance_inr=buyer_allowance_inr,
            memory=self._memory,
            min_discount_percent=self._min_discount_percent,
            max_add_on_share=self._max_add_on_share,
        )

        # Log the reasoning
        self._note(f"LLM buyer verdict: {verdict.verdict}")
        # Extract key rationale lines
        for line in verdict.rationale.split("\n"):
            stripped = line.strip()
            if stripped.startswith("-") or stripped.startswith("**"):
                self._note(f"  {stripped[:120]}")

        offer = verdict.offer
        if offer["type"] == "bundle_upsell":
            item_id = offer["item_id"]
            price = offer["unit_amount_paise"]
            self._note(
                f"merchant suggested add-on {item_id} at {price} paise -> {verdict.verdict}"
            )
        elif offer["type"] == "discount":
            pct = offer["percent"]
            self._note(
                f"merchant offered {pct:.1f}% off -> {verdict.verdict} "
                f"(my bar: {self._min_discount_percent:.0f}%)"
            )
        else:
            self._note("no offer presented by the merchant")

        if verdict.verdict == DECLINE and offer["type"] == "discount":
            self._note(
                "but I still want the item, so I'll complete the purchase at full price"
            )

        return verdict.verdict, verdict

    async def complete_session(self, session_id: str) -> dict[str, Any]:
        session = await self._fetch(session_id)
        total = _total_of(session)
        if total > self._max_allowance_paise:
            self._note(
                f"mandate check failed before completion ({total} > "
                f"{self._max_allowance_paise}); canceling"
            )
            return await self.cancel_session(session_id)

        response = await self._client.post(
            f"/checkout_sessions/{session_id}/complete",
            json={
                "payment_data": {
                    "token": self._payment_token,
                    "provider": "razorpay",
                }
            },
        )
        response.raise_for_status()
        completed = response.json()
        self.last_session = completed
        if completed["status"] == "completed":
            order_id = completed.get("order", {}).get("id")
            self._note(f"payment authorized; order {order_id}")
            # Record successful purchase in memory
            self._record_purchase(session, completed, accepted=True)
        else:
            reason = next(
                (m["content"] for m in completed.get("messages", []) if m["type"] == "error"),
                "completion refused",
            )
            self._note(f"completion failed: {reason}; not retrying")
            self._record_purchase(session, completed, accepted=False)
        return completed

    def _record_purchase(self, session: dict, completed: dict, accepted: bool) -> None:
        """Record the purchase outcome in memory."""
        for item in session.get("line_items", []):
            product_id = item.get("item", {}).get("id", "unknown")
            record = PurchaseRecord(
                session_id=completed.get("id", "unknown"),
                item_id=product_id,
                quantity=item.get("item", {}).get("quantity", 1),
                final_price_paise=item.get("total", 0),
                offered_price_paise=item.get("base_amount", 0),
                discount_percent=_effective_discount_percent(session) or 0.0,
                accepted=accepted,
            )
            self._memory.add(record)

    async def cancel_session(self, session_id: str) -> dict[str, Any]:
        response = await self._client.post(f"/checkout_sessions/{session_id}/cancel")
        response.raise_for_status()
        canceled = response.json()
        self.last_session = canceled
        self._note("canceled the checkout session")
        return canceled

    async def run_purchase(self, item_id: str, quantity: int = 1) -> PurchaseResult:
        """Full purchase flow: discover → session → review → decide → complete."""
        self.transcript = []
        await self.discover()
        session = await self.start_session(item_id, quantity)

        if session["status"] != "ready_for_payment":
            problems = "; ".join(m.get("content", "") for m in session.get("messages", []))
            self._note(f"merchant could not ready the session: {problems}")
            return PurchaseResult(session["status"], False, None, tuple(self.transcript))

        decision, verdict = self.review_offer(session)

        if decision == DECLINE:
            # Decline the offer but still buy at full price
            completed = await self.complete_session(session["id"])
            accepted_offer = False
        else:
            # ACCEPT: complete the purchase (with offer)
            completed = await self.complete_session(session["id"])
            accepted_offer = decision == ACCEPT and completed["status"] == "completed"

        return PurchaseResult(
            completed["status"],
            accepted_offer,
            completed.get("order"),
            tuple(self.transcript),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, session_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/checkout_sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    def _note(self, text: str) -> None:
        self.transcript.append(text)
