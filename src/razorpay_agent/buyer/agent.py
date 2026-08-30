from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2 as httpx

ACCEPT = "accept"
DECLINE = "decline"
NO_OFFER = "no_offer"


@dataclass(frozen=True)
class PurchaseResult:
    final_status: str
    accepted_offer: bool
    order: dict | None
    transcript: tuple[str, ...] = field(default_factory=tuple)


class BuyerAgent:
    def __init__(
        self,
        base_url: str,
        max_allowance_paise: int = 10_000_000,
        payment_token: str = "tok_ok",
        allowance_ttl_minutes: int = 30,
        min_worthwhile_discount_percent: float = 5.0,
        max_add_on_cart_share: float = 0.25,
        transport: Any | None = None,
    ) -> None:
        self._max_allowance_paise = max_allowance_paise
        self._payment_token = payment_token
        self._ttl_minutes = allowance_ttl_minutes
        self._min_discount_percent = min_worthwhile_discount_percent
        self._max_add_on_share = max_add_on_cart_share
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport)
        self.transcript: list[str] = []
        self.last_session: dict | None = None

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
                "expires_at": (
                    now + timedelta(minutes=self._ttl_minutes)
                ).isoformat(),
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

    def review_offer(self, session: dict[str, Any]) -> str:
        add_on = session.get("suggested_add_on")
        if add_on is not None:
            subtotal = _subtotal_of(session)
            within_share = add_on["unit_amount"] <= self._max_add_on_share * subtotal
            decision = ACCEPT if within_share else DECLINE
            self._note(
                f"merchant suggested add-on {add_on['item_id']} at "
                f"{add_on['unit_amount']} paise -> {decision}"
            )
            return decision

        discount_percent = _effective_discount_percent(session)
        if discount_percent is None:
            self._note("no offer presented by the merchant")
            return NO_OFFER
        decision = ACCEPT if discount_percent >= self._min_discount_percent else DECLINE
        self._note(
            f"merchant offered {discount_percent:.1f}% off -> {decision} "
            f"(my bar: {self._min_discount_percent:.0f}%)"
        )
        if decision == DECLINE and discount_percent > 0:
            self._note(
                "but I still want the item, so I'll complete the purchase at full price"
            )
        return decision

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
        else:
            reason = next(
                (m["content"] for m in completed.get("messages", []) if m["type"] == "error"),
                "completion refused",
            )
            self._note(f"completion failed: {reason}; not retrying")
        return completed

    async def cancel_session(self, session_id: str) -> dict[str, Any]:
        response = await self._client.post(f"/checkout_sessions/{session_id}/cancel")
        response.raise_for_status()
        canceled = response.json()
        self.last_session = canceled
        self._note("canceled the checkout session")
        return canceled

    async def run_purchase(self, item_id: str, quantity: int = 1) -> PurchaseResult:
        self.transcript = []
        await self.discover()
        session = await self.start_session(item_id, quantity)

        if session["status"] != "ready_for_payment":
            problems = "; ".join(m.get("content", "") for m in session.get("messages", []))
            self._note(f"merchant could not ready the session: {problems}")
            return PurchaseResult(session["status"], False, None, tuple(self.transcript))

        self.review_offer(session)
        completed = await self.complete_session(session["id"])
        accepted_offer = any("-> accept" in line for line in self.transcript)
        return PurchaseResult(
            completed["status"], accepted_offer, completed.get("order"), tuple(self.transcript)
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _fetch(self, session_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/checkout_sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    def _note(self, text: str) -> None:
        self.transcript.append(text)


def _subtotal_of(session: dict[str, Any]) -> int:
    for total in session.get("totals", []):
        if total["type"] == "subtotal":
            return int(total["amount"])
    return 0


def _total_of(session: dict[str, Any]) -> int:
    for total in session.get("totals", []):
        if total["type"] == "total":
            return int(total["amount"])
    return 0


def _effective_discount_percent(session: dict[str, Any]) -> float | None:
    for line in session.get("line_items", []):
        if line["discount"] > 0 and line["base_amount"] > 0:
            return 100.0 * line["discount"] / line["base_amount"]
    return None
