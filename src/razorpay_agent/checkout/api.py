import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from razorpay_agent.checkout.catalog import Product, find_product
from razorpay_agent.checkout.inventory import InventoryStore
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.payments import PaymentOutcome, PaymentProvider
from razorpay_agent.checkout.sessions import (
    CheckoutSessionState,
    SessionRepository,
)
from razorpay_agent.core.currency import INR, resolve_currency
from razorpay_agent.merchant import MERCHANT_NAME
from razorpay_agent.storefront import INDEX_HTML_PATH

STATUS_NOT_READY = "not_ready_for_payment"
STATUS_READY = "ready_for_payment"
STATUS_COMPLETED = "completed"
STATUS_CANCELED = "canceled"

SUPPORTED_PAYMENT_METHODS = ["card"]


def build_app(
    catalog: tuple[Product, ...],
    repository: SessionRepository,
    pipeline: OfferPipeline,
    payment_provider: PaymentProvider,
    eval_store=None,
    watchdog=None,
    inventory: InventoryStore | None = None,
) -> FastAPI:
    app = FastAPI(title="razorpay-agent", version="0.1.0")
    app.state.payment_provider = payment_provider

    # Tracks recent activity from an AI buyer-agent (signalled by the
    # `X-Razorpay-Agent` header) so the storefront can show an agent-vs-human badge.
    agent_presence: dict[str, float] = {"last_seen": 0.0}

    @app.middleware("http")
    async def track_agent_presence(request: Request, call_next):
        if request.headers.get("x-razorpay-agent"):
            agent_presence["last_seen"] = time.monotonic()
        return await call_next(request)

    @app.get("/storefront")
    def storefront() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))

    @app.get("/storefront/status")
    def storefront_status(mode: str | None = None) -> JSONResponse:
        forced = mode == "agent"
        active = forced or (time.monotonic() - agent_presence["last_seen"] < 30.0)
        return JSONResponse({"merchant_name": MERCHANT_NAME, "agent_active": active})

    @app.get("/watchdog/status")
    def watchdog_status() -> dict[str, Any]:
        if watchdog is None:
            return {"status": "watchdog_not_configured"}
        return watchdog.status()

    @app.post("/watchdog/promote")
    async def watchdog_promote(request: Request) -> dict[str, Any]:
        if watchdog is None:
            raise HTTPException(404, "watchdog not configured")
        body = await _json(request)
        note = body.get("note")
        try:
            watchdog.promote(str(note or ""))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"status": "promoted", "note": note}

    @app.get("/eval/offpolicy")
    def eval_offpolicy(alpha: float = 0.25) -> dict[str, Any]:
        if eval_store is None:
            return {"status": "eval_not_configured"}
        from razorpay_agent.eval.offpolicy import estimate_candidate_alpha
        from razorpay_agent.server import PRETRAINED_BANDIT_PATH

        snapshot = PRETRAINED_BANDIT_PATH
        import os

        if not os.path.exists(snapshot):
            return {
                "status": "no_policy_snapshot",
                "explanation": "off-policy evaluation anchors propensities to the pretrained policy snapshot",
            }
        return estimate_candidate_alpha(eval_store, snapshot, alpha_candidate=alpha)

    @app.get("/eval/report")
    def eval_report() -> dict[str, Any]:
        if eval_store is None:
            return {"status": "eval_not_configured"}
        from razorpay_agent.eval.report import latest_report

        report = latest_report(eval_store)
        if report is None:
            return {"status": "no_eval_run_recorded_yet", "honesty_note": None}
        import os

        from razorpay_agent.eval.offpolicy import estimate_candidate_alpha
        from razorpay_agent.server import PRETRAINED_BANDIT_PATH

        if os.path.exists(PRETRAINED_BANDIT_PATH):
            report["off_policy"] = estimate_candidate_alpha(
                eval_store, PRETRAINED_BANDIT_PATH, alpha_candidate=0.25
            )
        return report

    @app.get("/products")
    def product_feed() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": p.id,
                    "title": p.title,
                    "category": p.category,
                    "unit_amount": p.unit_amount_paise,
                    "currency": "inr",
                }
                for p in catalog
            ]
        }

    @app.post("/checkout_sessions", status_code=201)
    async def create_checkout_session(request: Request) -> dict[str, Any]:
        body = await _json(request)
        state = CheckoutSessionState(
            id=repository.new_id(),
            status=STATUS_NOT_READY,
            currency=INR,
            items=[],
            allowance_max_paise=0,
            allowance_expires_at=datetime.now(UTC),
        )
        problems: list[dict[str, Any]] = []

        allowance_error = _apply_allowance(state, body.get("allowance"))
        if allowance_error is not None:
            problems.append(_message("error", "invalid", "$.allowance", allowance_error))

        items_error = _apply_items(state, catalog, body.get("items"))
        if items_error is not None:
            problems.append(_message("error", "invalid", "$.items", items_error))

        if not problems:
            cart_paise, target_sku, category = _cart_summary(state.items, catalog)
            stagnant = False
            max_days = 0
            for entry in state.items:
                product = find_product(catalog, entry["product_id"])
                if product is not None and product.stagnant:
                    stagnant = True
                    if product.days_in_stock and product.days_in_stock > max_days:
                        max_days = product.days_in_stock
            state.is_stagnant = stagnant
            state.days_in_stock = max_days if stagnant else None
            if inventory is not None:
                items = [(e["product_id"], e["quantity"]) for e in state.items]
                if not inventory.reserve_for_session(state.id, items):
                    problems.append(
                        _message(
                            "error",
                            "out_of_stock",
                            "$.items",
                            "insufficient inventory for the requested quantity",
                        )
                    )
            if not problems:
                pipeline.propose_for_session(state, cart_paise, target_sku, category)
                state.status = STATUS_READY

        state.messages = problems
        repository.save(state)
        return _payload(state, catalog)

    @app.get("/checkout_sessions/{session_id}")
    def get_checkout_session(session_id: str) -> dict[str, Any]:
        state = _require_session(repository, session_id)
        return _payload(state, catalog)

    @app.post("/checkout_sessions/{session_id}")
    async def update_checkout_session(session_id: str, request: Request) -> dict[str, Any]:
        state = _require_session(repository, session_id)
        if state.status not in (STATUS_READY, STATUS_NOT_READY):
            raise HTTPException(409, f"session is {state.status}")
        body = await _json(request)

        if isinstance(body.get("items"), list):
            error = _apply_items(state, catalog, body["items"])
            if error is not None:
                state.messages = [_message("error", "invalid", "$.items", error)]
                repository.save(state)
                return _payload(state, catalog)

        if not state.items:
            state.messages = []
            repository.save(state)
            return _payload(state, catalog)

        if state.applied_offer is None:
            cart_paise, target_sku, category = _cart_summary(state.items, catalog)
            if inventory is not None:
                items = [(e["product_id"], e["quantity"]) for e in state.items]
                if not inventory.reserve_for_session(state.id, items):
                    state.messages = [
                        _message(
                            "error",
                            "out_of_stock",
                            "$.items",
                            "insufficient inventory for the requested quantity",
                        )
                    ]
                    repository.save(state)
                    return _payload(state, catalog)
            pipeline.propose_for_session(state, cart_paise, target_sku, category)

        state.messages = []
        state.status = STATUS_READY
        repository.save(state)
        return _payload(state, catalog)

    @app.post("/checkout_sessions/{session_id}/complete")
    async def complete_checkout_session(session_id: str, request: Request) -> dict[str, Any]:
        state = _require_session(repository, session_id)
        if state.status == STATUS_COMPLETED:
            return _payload(state, catalog)
        if state.status != STATUS_READY:
            raise HTTPException(409, f"session is {state.status}")
        body = await _json(request)
        payment_data = body.get("payment_data")
        token = payment_data.get("token") if isinstance(payment_data, dict) else None
        if not isinstance(token, str) or not token.strip():
            raise HTTPException(400, "payment_data.token is required")

        now = datetime.now(UTC)
        expired = state.allowance_expires_at <= now

        base_total = _base_total_paise(state.items, catalog)
        charge_amount = base_total - _discount_total(state) + _bundle_total(state)

        result = payment_provider.charge(charge_amount, state.currency.code, token)
        if result.outcome is PaymentOutcome.SUCCESS and not expired:
            state.status = STATUS_COMPLETED
            state.order = {
                "id": result.provider_reference,
                "checkout_session_id": state.id,
                "permalink_url": f"https://shop.example.test/orders/{state.id}",
            }
            if inventory is not None:
                inventory.commit_session(state.id)
            pipeline.resolve_accepted(state, charge_amount, base_total)
            state.applied_offer = None
            repository.save(state)
            return _payload(state, catalog)

        reason = (
            "allowance expired"
            if expired
            else "payment declined by provider"
            if result.outcome is PaymentOutcome.DECLINED
            else "payment credential no longer valid"
        )
        if inventory is not None:
            inventory.release_session(state.id)
        pipeline.resolve_failed(state, f"{reason}; offer rolled back")
        state.applied_offer = None
        state.status = STATUS_NOT_READY
        state.messages = [
            _message("error", "payment_declined", "$.payment_data", reason.title())
        ]
        repository.save(state)
        return _payload(state, catalog)

    @app.post("/checkout_sessions/{session_id}/cancel")
    def cancel_checkout_session(session_id: str) -> dict[str, Any]:
        state = _require_session(repository, session_id)
        if state.status in (STATUS_COMPLETED, STATUS_CANCELED):
            raise HTTPException(409, f"session already {state.status}")
        if state.applied_offer is not None and state.applied_offer.gate_decision.allowed:
            pipeline.resolve_declined(state, "buyer canceled after offer")
        if inventory is not None:
            inventory.release_session(state.id)
        state.applied_offer = None
        state.status = STATUS_CANCELED
        repository.save(state)
        return _payload(state, catalog)

    return app


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be a JSON object")
    return body


def _require_session(repository: SessionRepository, session_id: str) -> CheckoutSessionState:
    state = repository.get(session_id)
    if state is None:
        raise HTTPException(404, "unknown checkout session")
    return state


def _apply_allowance(state: CheckoutSessionState, allowance: Any) -> str | None:
    if not isinstance(allowance, dict):
        return "allowance object is required"
    max_amount = allowance.get("max_amount")
    expires_at_raw = allowance.get("expires_at")
    currency_code = allowance.get("currency", "inr")
    if not isinstance(max_amount, int) or isinstance(max_amount, bool) or max_amount <= 0:
        return "allowance.max_amount must be a positive integer of minor units"
    try:
        currency = resolve_currency(currency_code)
    except Exception:
        return f"unsupported allowance.currency {currency_code!r}"
    try:
        expires_at = datetime.fromisoformat(str(expires_at_raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "allowance.expires_at must be an RFC 3339 timestamp"
    if expires_at.tzinfo is None:
        return "allowance.expires_at must carry a timezone offset"
    state.currency = currency
    state.allowance_max_paise = max_amount
    state.allowance_expires_at = expires_at
    return None


def _apply_items(
    state: CheckoutSessionState, catalog: tuple[Product, ...], raw_items: Any
) -> str | None:
    if not isinstance(raw_items, list) or not raw_items:
        return "items must be a non-empty list"
    parsed: list[dict] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            return "each item must be an object"
        product = find_product(catalog, entry.get("id"))
        quantity = entry.get("quantity", 1)
        if product is None:
            return f"unknown item id {entry.get('id')!r}"
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            return f"quantity for {product.id} must be a positive integer"
        parsed.append({"product_id": product.id, "quantity": quantity})
    state.items = parsed
    return None


def _cart_summary(items: list[dict], catalog: tuple[Product, ...]) -> tuple[int, str, str]:
    total = 0
    for entry in items:
        product = find_product(catalog, entry["product_id"])
        total += product.unit_amount_paise * entry["quantity"]
    first = find_product(catalog, items[0]["product_id"])
    return total, first.id, first.category


def _base_total_paise(items: list[dict], catalog: tuple[Product, ...]) -> int:
    total = 0
    for entry in items:
        product = find_product(catalog, entry["product_id"])
        total += product.unit_amount_paise * entry["quantity"]
    return total


def _discount_total(state: CheckoutSessionState) -> int:
    offer = state.applied_offer
    if offer is None or not offer.gate_decision.allowed:
        return 0
    return offer.discount_paise


def _bundle_total(state: CheckoutSessionState) -> int:
    offer = state.applied_offer
    if offer is None or not offer.gate_decision.allowed:
        return 0
    return offer.bundle_price_paise


def _message(kind: str, code: str, path: str, content: str) -> dict[str, Any]:
    return {"type": kind, "code": code, "path": path, "content_type": "plain", "content": content}


def _payload(state: CheckoutSessionState, catalog: tuple[Product, ...]) -> dict[str, Any]:
    base_total = 0
    line_items: list[dict[str, Any]] = []
    for index, entry in enumerate(state.items):
        product = find_product(catalog, entry["product_id"])
        base = product.unit_amount_paise * entry["quantity"]
        discount = 0
        offer = state.applied_offer
        if (
            offer is not None
            and offer.gate_decision.allowed
            and offer.discount_paise > 0
            and offer.proposed_action.target == product.id
        ):
            discount = min(offer.discount_paise, base)
        line_items.append(
            {
                "id": f"line_item_{index}_{product.id}",
                "item": {"id": product.id, "quantity": entry["quantity"]},
                "base_amount": base,
                "discount": discount,
                "subtotal": base - discount,
                "tax": 0,
                "total": base - discount,
            }
        )
        base_total += base

    discount_total = sum(line["discount"] for line in line_items)

    add_on_amount = 0
    offer = state.applied_offer
    if (
        offer is not None
        and offer.gate_decision.allowed
        and offer.proposed_action.action_type == "bundle_upsell"
    ):
        add_on_amount = offer.bundle_price_paise
        line_items.append(
            {
                "id": "line_item_add_on",
                "item": {
                    "id": offer.proposed_action.bundle_item,
                    "quantity": 1,
                },
                "base_amount": add_on_amount,
                "discount": 0,
                "subtotal": add_on_amount,
                "tax": 0,
                "total": add_on_amount,
            }
        )

    grand_total = base_total - discount_total + add_on_amount

    totals: list[dict[str, Any]] = [
        {
            "type": "items_base_amount",
            "display_text": "Item(s) total",
            "amount": base_total,
        }
    ]
    if discount_total > 0:
        totals.append(
            {
                "type": "items_discount",
                "display_text": "Offer discount",
                "amount": -discount_total,
            }
        )
    if add_on_amount > 0:
        totals.append(
            {
                "type": "add_on",
                "display_text": "Recommended add-on",
                "amount": add_on_amount,
            }
        )
    totals.extend(
        [
            {"type": "subtotal", "display_text": "Subtotal", "amount": grand_total},
            {"type": "total", "display_text": "Total", "amount": grand_total},
        ]
    )

    payload: dict[str, Any] = {
        "id": state.id,
        "status": state.status,
        "currency": state.currency.code,
        "line_items": line_items,
        "totals": totals,
        "fulfillment_options": [],
        "messages": state.messages,
        "links": [],
        "payment_provider": {
            "provider": "razorpay",
            "supported_payment_methods": SUPPORTED_PAYMENT_METHODS,
        },
    }

    if offer is not None and offer.gate_decision.allowed:
        action = offer.proposed_action
        if action.action_type == "bundle_upsell":
            payload["suggested_add_on"] = {
                "item_id": action.bundle_item,
                "unit_amount": int(round(float(action.bundle_price) * state.currency.minor_unit_divisor)),
                "currency": state.currency.code,
            }
            payload["messages"].append(
                _message("info", "offer", "$", "Add-on available for this checkout.")
            )

    if state.order is not None:
        payload["order"] = state.order
    return payload
