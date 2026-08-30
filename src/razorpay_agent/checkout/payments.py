from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

SUCCESS = "success"
DECLINED = "declined"
EXPIRED = "expired_token"


class PaymentOutcome(Enum):
    SUCCESS = SUCCESS
    DECLINED = DECLINED
    EXPIRED_TOKEN = EXPIRED


@dataclass(frozen=True)
class ChargeResult:
    outcome: PaymentOutcome
    provider_reference: str


@dataclass(frozen=True)
class OrderStatusReport:
    reference: str
    status: str
    amount_paid_paise: int
    raw: dict


class PaymentProvider:
    name: str = "razorpay"

    def charge(self, amount_paise: int, currency: str, token: str) -> ChargeResult:
        raise NotImplementedError

    def order_status(self, provider_reference: str) -> OrderStatusReport:
        raise NotImplementedError

    def create_payment_link(
        self, amount_paise: int, currency: str, description: str, reference: str
    ) -> dict:
        raise NotImplementedError

    def payment_link_status(self, link_id: str) -> dict:
        raise NotImplementedError


class ScriptedPaymentProvider(PaymentProvider):
    name = "scripted"

    def __init__(self, scripted: dict[str, PaymentOutcome] | None = None) -> None:
        self._scripted = scripted or {}
        self.calls: list[tuple[int, str, str]] = []
        self._links: dict[str, dict] = {}

    def charge(self, amount_paise: int, currency: str, token: str) -> ChargeResult:
        self.calls.append((amount_paise, currency, token))
        outcome = self._scripted.get(token, PaymentOutcome.SUCCESS)
        return ChargeResult(outcome, f"test_ref_{token}")

    def order_status(self, provider_reference: str) -> OrderStatusReport:
        return OrderStatusReport(provider_reference, "created", 0, {"note": "scripted provider"})

    def create_payment_link(
        self, amount_paise: int, currency: str, description: str, reference: str
    ) -> dict:
        link_id = f"scripted_link_{len(self._links) + 1}"
        record = {
            "id": link_id,
            "url": f"https://payment.example.test/{link_id}",
            "status": "created",
        }
        self._links[link_id] = record
        return record

    def payment_link_status(self, link_id: str) -> dict:
        return dict(self._links.get(link_id, {"id": link_id, "status": "unknown"}))


class RazorpayTestProvider(PaymentProvider):
    DEMO_DECLINE_TOKENS = ("tok_declined", "tok_bad")
    DEMO_EXPIRED_TOKENS = ("tok_expired",)

    def __init__(self, key_id: str, key_secret: str, client=None) -> None:
        import razorpay

        self.name = "razorpay"
        self._client = client if client is not None else razorpay.Client(auth=(key_id, key_secret))

    def charge(self, amount_paise: int, currency: str, token: str) -> ChargeResult:
        if token in self.DEMO_DECLINE_TOKENS:
            return ChargeResult(PaymentOutcome.DECLINED, f"demo_declined_{token}")
        if token in self.DEMO_EXPIRED_TOKENS:
            return ChargeResult(PaymentOutcome.EXPIRED_TOKEN, f"demo_expired_{token}")
        # Settlement note (intentional, per architecture.md §4.3): in test mode an
        # order is created here, but the real capture happens when the buyer pays
        # through the hosted Payment Link (see create_payment_link). The merchant
        # Orders API entry therefore remains `created` until a Standard Checkout
        # integration binds capture to this same order id. We do NOT fabricate a
        # capture event — doing so would misrepresent settlement.
        order = self._client.order.create(
            {
                "amount": amount_paise,
                "currency": currency.upper(),
                "payment_capture": 1,
                "receipt": token[:40],
            }
        )
        return ChargeResult(PaymentOutcome.SUCCESS, order["id"])

    def order_status(self, provider_reference: str) -> OrderStatusReport:
        order = self._client.order.fetch(provider_reference)
        return OrderStatusReport(
            reference=order["id"],
            status=order["status"],
            amount_paid_paise=int(order.get("amount_paid", 0)),
            raw=dict(order),
        )

    def create_payment_link(
        self, amount_paise: int, currency: str, description: str, reference: str
    ) -> dict:
        link = self._client.payment_link.create(
            {
                "amount": amount_paise,
                "currency": currency.upper(),
                "accept_partial": False,
                "reference_id": reference[:40],
                "description": description[:200],
            }
        )
        return {"id": link["id"], "url": link["short_url"], "status": link["status"]}

    def payment_link_status(self, link_id: str) -> dict:
        link = self._client.payment_link.fetch(link_id)
        return {"id": link["id"], "url": link.get("short_url"), "status": link["status"]}
