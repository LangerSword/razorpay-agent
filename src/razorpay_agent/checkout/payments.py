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

    def __init__(self, scripted: dict[str, PaymentOutcome] | None = None, fail_link_creation: bool = False) -> None:
        self._scripted = scripted or {}
        self.calls: list[tuple[int, str, str]] = []
        self._links: dict[str, dict] = {}
        self._fail_link_creation = fail_link_creation

    def charge(self, amount_paise: int, currency: str, token: str) -> ChargeResult:
        self.calls.append((amount_paise, currency, token))
        outcome = self._scripted.get(token, PaymentOutcome.SUCCESS)
        return ChargeResult(outcome, f"test_ref_{token}")

    def order_status(self, provider_reference: str) -> OrderStatusReport:
        return OrderStatusReport(provider_reference, "created", 0, {"note": "scripted provider"})

    def create_payment_link(
        self, amount_paise: int, currency: str, description: str, reference: str
    ) -> dict:
        if self._fail_link_creation:
            raise RuntimeError("Payment link creation failed: simulated gateway timeout")
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
    DEFAULT_TIMEOUT = 20.0

    def __init__(
        self, key_id: str, key_secret: str, client=None, timeout: float = DEFAULT_TIMEOUT
    ) -> None:
        import razorpay

        self.name = "razorpay"
        self._timeout = timeout
        self._key_id = key_id
        self._client = client if client is not None else razorpay.Client(auth=(key_id, key_secret))

    def charge(self, amount_paise: int, currency: str, token: str) -> ChargeResult:
        if token in self.DEMO_DECLINE_TOKENS:
            return ChargeResult(PaymentOutcome.DECLINED, f"demo_declined_{token}")
        if token in self.DEMO_EXPIRED_TOKENS:
            return ChargeResult(PaymentOutcome.EXPIRED_TOKEN, f"demo_expired_{token}")
        # Standard Checkout: create an order (no payment link needed)
        import concurrent.futures

        def _create_order() -> dict:
            return self._client.order.create(
                {
                    "amount": amount_paise,
                    "currency": currency.upper(),
                    "payment_capture": 1,
                    "receipt": token[:40],
                }
            )

        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(_create_order)
                order = future.result(timeout=self._timeout)
        except Exception:
            return ChargeResult(
                PaymentOutcome.DECLINED,
                f"razorpay_error_{token}",
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
        """Create a Standard Checkout URL (hosted payment page).
        
        Uses Razorpay's Standard Checkout instead of Payment Links to avoid
        the 30-link test mode limit. The hosted page handles UPI/card/netbanking
        and fires webhooks on payment completion.
        """
        order = self._client.order.create(
            {
                "amount": amount_paise,
                "currency": currency.upper(),
                "payment_capture": 1,
                "receipt": reference[:40],
            }
        )
        checkout_url = f"https://checkout.razorpay.com/v1/payment/{order['id']}?key_id={self._key_id}"
        return {
            "id": order["id"],
            "url": checkout_url,
            "status": "created",
            "order_id": order["id"],
        }

    def payment_link_status(self, link_id: str) -> dict:
        """Check order status by ID (Standard Checkout uses orders, not links)."""
        try:
            order = self._client.order.fetch(link_id)
            return {
                "id": order["id"],
                "url": f"https://checkout.razorpay.com/v1/payment/{order['id']}?key_id={self._key_id}",
                "status": order["status"],
                "amount_paid": order.get("amount_paid", 0),
            }
        except Exception:
            return {"id": link_id, "status": "unknown"}
