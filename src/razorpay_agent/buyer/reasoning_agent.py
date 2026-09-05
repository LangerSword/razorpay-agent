from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

ACCEPT = "accept"
DECLINE = "decline"
NO_OFFER = "no_offer"


@dataclass(frozen=True)
class PurchaseResult:
    final_status: str
    accepted_offer: bool
    order: dict | None
    transcript: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Memory — the buyer remembers what it bought and what it was offered
# ---------------------------------------------------------------------------


@dataclass
class PurchaseRecord:
    session_id: str
    item_id: str
    quantity: int
    final_price_paise: int
    offered_price_paise: int
    discount_percent: float
    accepted: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class PurchaseMemory:
    """Simple in-memory purchase history for the buyer agent.

    Production would persist this to a side table; for the MVP it lives in
    process memory and can be serialized to JSON for the demo.
    """

    def __init__(self, history: list[PurchaseRecord] | None = None) -> None:
        self._history: list[PurchaseRecord] = history or []

    @property
    def history(self) -> list[PurchaseRecord]:
        return list(self._history)

    def add(self, record: PurchaseRecord) -> None:
        self._history.append(record)

    def has_purchased(self, item_id: str, within_days: int = 30) -> bool:
        cutoff = datetime.now(UTC) - timedelta(days=within_days)
        return any(
            r.item_id == item_id and r.accepted and r.timestamp > cutoff
            for r in self._history
        )

    def recent_items(self, n: int = 5) -> list[str]:
        return [r.item_id for r in self._history[-n:] if r.accepted]

    def to_dict(self) -> dict[str, Any]:
        return {
            "purchase_count": len(self._history),
            "recent_items": self.recent_items(10),
            "total_spent_paise": sum(r.final_price_paise for r in self._history if r.accepted),
        }


# ---------------------------------------------------------------------------
# Buyer-side reasoner — single-pass LLM evaluation of a merchant offer
# ---------------------------------------------------------------------------


def _build_prompt(
    session: dict,
    cart_value_inr: float,
    buyer_allowance_inr: float,
    memory: PurchaseMemory,
    min_discount_percent: float,
    max_add_on_share: float,
) -> str:
    offer = _summarize_offer(session)
    mem_summary = memory.to_dict()

    # For bundles, base_cart_inr is the original item price (add-on is separate)
    base_cart_inr = cart_value_inr
    if offer["type"] == "bundle_upsell":
        # add_on is a separate item being added; share = add_on / base_cart
        base_cart_inr = cart_value_inr
    
    if offer["type"] == "discount":
        effective_price = base_cart_inr * (1 - offer["percent"] / 100)
        savings = base_cart_inr - effective_price
        offer_desc = (
            f"A {offer['percent']:.1f}% discount on the {offer['target_sku']} "
            f"(save INR {savings:.2f}, final price INR {effective_price:.2f})"
        )
    elif offer["type"] == "bundle_upsell":
        effective_price = base_cart_inr + offer["unit_amount_paise"] / 100.0
        offer_desc = (
            f"A bundle add-on: {offer['item_id']} at INR {offer['unit_amount_paise'] / 100:.2f} "
            f"(total would be INR {effective_price:.2f})"
        )
    else:
        offer_desc = "No offer — just the base item at full price."

    return (
        "You are a buyer agent deciding whether to accept a merchant's offer.\n"
        "The purchase will happen regardless — your decision is whether to take the OFFER or reject it and buy at full price.\n\n"
        "SESSION DETAILS:\n"
        f"- Item: {offer.get('target_sku', 'unknown')}\n"
        f"- Cart value (full price): INR {base_cart_inr:.2f}\n"
        f"- Buyer spending allowance: INR {buyer_allowance_inr:.2f}\n"
        f"- Offer from merchant: {offer_desc}\n"
        f"- Recently purchased: {mem_summary['recent_items'] or 'nothing yet'}\n"
        f"- Total spent this window: INR {mem_summary['total_spent_paise'] / 100:.2f}\n"
        f"- Your minimum worthwhile discount: {min_discount_percent:.1f}%\n"
        f"- Your max acceptable add-on share: {max_add_on_share:.0%} of cart\n\n"
        "DECISION:\n"
        "- ACCEPT: the offer is good — take the discount/add-on as proposed.\n"
        "- DECLINE: the offer is bad — reject it and buy the base item at full price instead.\n"
        "(You never walk away from the purchase itself in this eval.)\n\n"
        "REASONING: Write 1-3 sentences explaining your decision.\n"
        "Then output exactly one of:\n"
        "  Verdict: ACCEPT\n"
        "  Verdict: DECLINE\n"
    )


def _parse_verdict(text: str) -> str:
    """Strictly parse verdict from LLM output.

    Requires a line that starts exactly with 'Verdict: ACCEPT' or 'Verdict: DECLINE'.
    Also maps merchant-style APPROVE/REJECT for stub compatibility.
    """
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "Verdict: ACCEPT":
            return ACCEPT
        if stripped == "Verdict: DECLINE":
            return DECLINE
        # Stub compatibility
        if stripped == "Verdict: APPROVE":
            return ACCEPT
        if stripped == "Verdict: REJECT":
            return DECLINE
    # If no strict match found, check if the model used a variant
    low = text.lower()
    if "verdict: accept" in low or "verdict: approve" in low:
        return ACCEPT
    if "verdict: decline" in low or "verdict: reject" in low:
        return DECLINE
    # Safe default — if unclear, reject the offer
    return DECLINE


def evaluate_offer(
    llm: Any,
    session: dict,
    cart_value_inr: float,
    buyer_allowance_inr: float,
    memory: PurchaseMemory,
    min_discount_percent: float = 5.0,
    max_add_on_share: float = 0.25,
) -> "BuyerVerdict":
    prompt = _build_prompt(
        session,
        cart_value_inr,
        buyer_allowance_inr,
        memory,
        min_discount_percent,
        max_add_on_share,
    )
    response = llm.complete(prompt)
    verdict = _parse_verdict(response)
    offer = _summarize_offer(session)
    return BuyerVerdict(verdict=verdict, rationale=response, offer=offer)


@dataclass(frozen=True)
class BuyerVerdict:
    verdict: str  # ACCEPT | DECLINE
    rationale: str
    offer: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_offer(session: dict) -> dict:
    target_sku = session.get("target_sku", "unknown")
    offer: dict[str, Any] = {"type": "none", "target_sku": target_sku}
    if session.get("suggested_add_on"):
        ao = session["suggested_add_on"]
        offer = {
            "type": "bundle_upsell",
            "item_id": ao["item_id"],
            "unit_amount_paise": ao["unit_amount"],
            "target_sku": target_sku,
        }
    else:
        for line in session.get("line_items", []):
            if line.get("discount", 0) > 0:
                offer = {
                    "type": "discount",
                    "discount_paise": line["discount"],
                    "base_amount_paise": line["base_amount"],
                    "percent": 100.0 * line["discount"] / line["base_amount"] if line["base_amount"] > 0 else 0,
                    "target_sku": target_sku,
                }
                break
    return offer


def _subtotal_of(session: dict) -> int:
    for total in session.get("totals", []):
        if total["type"] == "subtotal":
            return int(total["amount"])
    return 0


def _total_of(session: dict) -> int:
    for total in session.get("totals", []):
        if total["type"] == "total":
            return int(total["amount"])
    return 0


def _effective_discount_percent(session: dict) -> float | None:
    for line in session.get("line_items", []):
        if line["discount"] > 0 and line["base_amount"] > 0:
            return 100.0 * line["discount"] / line["base_amount"]
    return None
