import asyncio
import json
import queue
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from razorpay_agent.buyer import CartBuyerAgent, Personality
from razorpay_agent.checkout.catalog import Product, find_product
from razorpay_agent.checkout.inventory import InventoryStore
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.payments import (
    PaymentOutcome,
    PaymentProvider,
    ScriptedPaymentProvider,
)
from razorpay_agent.checkout.sessions import CheckoutSessionState, SessionRepository
from razorpay_agent.core.currency import INR, resolve_currency
from razorpay_agent.merchant import MERCHANT_NAME
from razorpay_agent.storefront import INDEX_HTML_PATH, REACT_BUILD_PATH

# Global thread-safe event bus for SSE and buyer message history
_event_bus: queue.Queue = queue.Queue()
_event_lock = threading.Lock()
_buyer_messages: list[dict] = []
_buyer_messages_lock = threading.Lock()
_BUYER_MSG_LIMIT = 100

# Payment webhook events: session_id -> asyncio.Event (set when webhook fires)
_payment_events: dict[str, asyncio.Event] = {}
_payment_events_lock = threading.Lock()


def _record_buyer_message(msg: str, agent_name: str = "BuyerAgent") -> None:
    """Record a buyer reasoning message to the ring buffer for polling."""
    with _buyer_messages_lock:
        _buyer_messages.append({
            "type": "reasoning",
            "agent": agent_name,
            "message": msg,
            "timestamp": datetime.now(UTC).isoformat(),
        })
        if len(_buyer_messages) > _BUYER_MSG_LIMIT:
            _buyer_messages.pop(0)


def _get_buyer_messages(limit: int = 50, offset: int = 0) -> list[dict]:
    """Get recent buyer reasoning messages, with optional offset for pagination."""
    with _buyer_messages_lock:
        msgs = list(_buyer_messages)
        if offset:
            msgs = msgs[offset:]
        return msgs[-limit:]


STATUS_NOT_READY = "not_ready_for_payment"
STATUS_READY = "ready_for_payment"
STATUS_COMPLETED = "completed"
STATUS_CANCELED = "canceled"

SUPPORTED_PAYMENT_METHODS = ["card"]


def _register_payment_event(session_id: str) -> asyncio.Event:
    """Register an event for a session that will be set when the payment webhook fires."""
    event = asyncio.Event()
    with _payment_events_lock:
        _payment_events[session_id] = event
    return event


def _signal_payment_event(session_id: str) -> None:
    """Signal that a payment webhook has fired for a session."""
    with _payment_events_lock:
        event = _payment_events.get(session_id)
    if event is not None:
        event.set()


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


CHECKOUT_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>razorpay-agent · Checkout</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0a0c10; color: #e8eaed; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; }
    .card { background: #12151b; border: 1px solid #252a34; border-radius: 12px; padding: 32px; max-width: 420px; width: 100%; text-align: center; }
    .amount { font-size: 32px; font-weight: 800; color: #4a9eff; margin: 16px 0; }
    .amount.original { font-size: 18px; color: #7a8090; text-decoration: line-through; margin-bottom: 4px; }
    .amount.final { font-size: 32px; font-weight: 800; color: #4ade80; margin-top: 4px; }
    .btn { display: inline-block; padding: 14px 28px; background: #4a9eff; color: #000; border: none; border-radius: 8px; font-size: 16px; font-weight: 700; cursor: pointer; width: 100%; margin-top: 16px; }
    .btn:hover { filter: brightness(1.1); }
    .items { text-align: left; margin: 16px 0; padding: 12px; background: #1a1e26; border-radius: 8px; }
    .item { display: flex; justify-content: space-between; padding: 4px 0; font-size: 14px; }
    .status { margin-top: 16px; padding: 12px; border-radius: 8px; font-weight: 600; display: none; }
    .status.success { background: rgba(74,222,128,0.15); color: #4ade80; display: block; }
    .status.error { background: rgba(248,113,113,0.15); color: #f87171; display: block; }
    .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.3); border-top-color: #fff; border-radius: 50%; animation: spin 0.6s linear infinite; vertical-align: middle; margin-right: 8px; }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>
  <div class="card">
    <div style="font-size: 14px; color: #7a8090;">General Goods Co.</div>
    <div class="amount original" id="originalAmount">₹0.00</div>
    <div class="amount final" id="finalAmount">₹0.00</div>
    <div class="items" id="items"></div>
    <button class="btn" id="payBtn" onclick="payNow()">Pay with Razorpay</button>
    <div class="status" id="status"></div>
  </div>
  <script src="https://checkout.razorpay.com/v1/checkout.js"></script>
  <script>
    const orderId = '__ORDER_ID__';
    const keyId = '__KEY_ID__';
    const amount = parseInt('__AMOUNT__') || 0;
    const finalAmount = parseInt('__FINAL_AMOUNT__') || amount;
    const items = __ITEMS__;

    document.getElementById('originalAmount').textContent = '₹' + (amount / 100).toFixed(2);
    document.getElementById('finalAmount').textContent = '₹' + (finalAmount / 100).toFixed(2);
    document.getElementById('items').innerHTML = items.map(i =>
      '<div class="item"><span>' + i.title + '</span><span>₹' + (i.price/100).toFixed(0) + '</span></div>'
    ).join('');

    function payNow() {
      const btn = document.getElementById('payBtn');
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>Opening checkout...';
      const options = {
        key: keyId,
        amount: amount,
        currency: 'INR',
        name: 'General Goods Co.',
        description: items.map(i => i.title).join(', '),
        order_id: orderId,
        handler: function(response) {
          document.getElementById('status').className = 'status success';
          document.getElementById('status').textContent = 'Payment successful! Payment ID: ' + response.razorpay_payment_id;
          btn.textContent = 'Paid';
          btn.style.background = '#4ade80';
        },
        prefill: { email: 'test@example.com', contact: '+919999999999' },
        theme: { color: '#4a9eff' },
        modal: { ondismiss: function() {
          document.getElementById('status').className = 'status error';
          document.getElementById('status').textContent = 'Payment cancelled';
          btn.disabled = false;
          btn.textContent = 'Pay with Razorpay';
        }}
      };
      const rzp = new Razorpay(options);
      rzp.open();
    }
  </script>
</body>
</html>
"""


def build_app(
    catalog: tuple[Product, ...],
    repository: SessionRepository,
    pipeline: OfferPipeline,
    payment_provider: PaymentProvider,
    eval_store=None,
    watchdog=None,
    inventory: InventoryStore | None = None,
    audit_store=None,
) -> FastAPI:
    app = FastAPI(title="razorpay-agent", version="0.1.0")

    # Mount React build assets if available
    REACT_ASSETS_PATH = Path(__file__).resolve().parent.parent.parent.parent / "web" / "dist" / "assets"
    if REACT_ASSETS_PATH.exists():
        app.mount("/assets", StaticFiles(directory=str(REACT_ASSETS_PATH)), name="react-assets")

    app.state.payment_provider = payment_provider

    agent_presence: dict[str, float] = {"last_seen": 0.0}

    @app.middleware("http")
    async def track_agent_presence(request: Request, call_next):
        if request.headers.get("x-razorpay-agent"):
            agent_presence["last_seen"] = time.monotonic()
        return await call_next(request)

    @app.get("/storefront")
    def storefront() -> HTMLResponse:
        if REACT_BUILD_PATH.exists():
            return HTMLResponse(REACT_BUILD_PATH.read_text(encoding="utf-8"))
        return HTMLResponse(INDEX_HTML_PATH.read_text(encoding="utf-8"))

    @app.get("/checkout")
    def checkout_page(order_id: str | None = None, key_id: str | None = None, amount: str = "0", items: str = "[]", final_amount: str = "0") -> HTMLResponse:
        """Serve the Razorpay checkout page with order details."""
        try:
            amount_int = int(amount)
        except (ValueError, TypeError):
            amount_int = 0
        try:
            final_int = int(final_amount)
        except (ValueError, TypeError):
            final_int = amount_int
        
        html = CHECKOUT_HTML
        html = html.replace("__ORDER_ID__", order_id or "")
        html = html.replace("__KEY_ID__", key_id or "")
        html = html.replace("__AMOUNT__", str(amount_int))
        html = html.replace("__FINAL_AMOUNT__", str(final_int))
        html = html.replace("__ITEMS__", items)
        return HTMLResponse(html)

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
        import os

        from razorpay_agent.eval.offpolicy import estimate_candidate_alpha
        from razorpay_agent.server import PRETRAINED_BANDIT_PATH
        if not os.path.exists(PRETRAINED_BANDIT_PATH):
            return {
                "status": "no_policy_snapshot",
                "explanation": "off-policy evaluation anchors propensities to the pretrained policy snapshot",
            }
        return estimate_candidate_alpha(eval_store, PRETRAINED_BANDIT_PATH, alpha_candidate=alpha)

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

    @app.get("/api/events")
    def api_events(limit: int = 20) -> dict[str, Any]:
        if audit_store is None:
            return {"status": "audit_not_configured"}
        try:
            limit = min(max(limit, 1), 50)
        except Exception:
            limit = 20
        entries = audit_store.recent(limit=limit)
        return {
            "events": [
                {
                    "timestamp": e.timestamp,
                    "session_id": e.session_id,
                    "action_type": e.proposed_action.action_type,
                    "source": e.proposed_action.source,
                    "outcome": e.outcome.status,
                    "detail": e.outcome.detail,
                    "allowed": e.gate_decision.allowed,
                    "reason": e.gate_decision.reason,
                    "discount_percent": e.proposed_action.discount_percent,
                    "bundle_item": e.proposed_action.bundle_item,
                    "bundle_price": e.proposed_action.bundle_price,
                }
                for e in entries
            ]
        }

    @app.post("/api/demo/start")
    def api_demo_start() -> dict[str, Any]:
        import os
        import subprocess
        import sys
        try:
            result = subprocess.run(
                ['pgrep', '-f', 'python.*demo.run_full_demo'],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split('\n')
                for pid in pids:
                    try:
                        ps_result = subprocess.run(
                            ['ps', '-p', pid, '-o', 'cmd='],
                            capture_output=True, text=True
                        )
                        if ps_result.returncode == 0 and 'run_full_demo' in ps_result.stdout and 'python' in ps_result.stdout:
                            return {"status": "already_running", "message": "Demo is already running"}
                    except Exception:
                        pass
        except Exception:
            pass
        api_dir = os.path.dirname(os.path.abspath(__file__))
        repo_root = api_dir
        for _ in range(5):
            if os.path.isdir(os.path.join(repo_root, 'demo')):
                break
            repo_root = os.path.dirname(repo_root)
        try:
            pid = os.fork()
            if pid == 0:
                os.chdir(repo_root)
                os.umask(0)
                os.setsid()
                with open('/tmp/razorpay_demo.log', 'w') as f:
                    os.dup2(f.fileno(), 1)
                    os.dup2(f.fileno(), 2)
                os.execv(sys.executable, [sys.executable, 'demo/run_full_demo.py', '--skip-payment'])
            else:
                return {"status": "started", "message": f"Demo started (PID: {pid})"}
        except Exception as exc:
            return {"status": "error", "message": f"Failed to start demo: {exc}"}

    @app.post("/api/autonomous/start")
    async def api_autonomous_start(request: Request) -> dict[str, Any]:
        body = await _json(request)
        interests = body.get("interests", "")
        avoid = body.get("avoid", "")
        budget = int(body.get("budget", 10000))
        min_discount = float(body.get("min_discount", 5.0))
        impulsiveness = float(body.get("impulsiveness", 0.3))
        patience = int(body.get("patience", 8))
        style = body.get("style", "analytical")
        fail_payment_link = body.get("fail_payment_link", False)
        demo_auto_pay = body.get("demo_auto_pay", True)
        if isinstance(fail_payment_link, str):
            fail_payment_link = fail_payment_link.lower() in ("1", "true", "yes")
        if isinstance(demo_auto_pay, str):
            demo_auto_pay = demo_auto_pay.lower() in ("1", "true", "yes")
        curated_ids = body.get("curated_ids", "")

        interest_list = [i.strip() for i in interests.split(',') if i.strip()]
        avoid_list = [a.strip() for a in avoid.split(',') if a.strip()]
        curated_list = [c.strip() for c in curated_ids.split(',') if c.strip()] if curated_ids else None

        personality = Personality(
            name=f"AutoBuyer-{style}",
            interests=interest_list,
            avoid=avoid_list,
            budget_paise=budget * 100,
            min_discount_percent=min_discount,
            impulsiveness=impulsiveness,
            patience=patience,
            style=style,
            demo_auto_pay=demo_auto_pay,
        )

        if fail_payment_link:
            if isinstance(payment_provider, ScriptedPaymentProvider):
                payment_provider._fail_link_creation = True

        def run_dual_agents():
            with _buyer_messages_lock:
                _buyer_messages.clear()

            def buyer_callback(msg: str):
                _record_buyer_message(msg, personality.name)
                try:
                    _event_bus.put_nowait({
                        "type": "reasoning",
                        "agent": personality.name,
                        "message": msg,
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                except Exception:
                    pass

            import httpx2 as httpx
            buyer_agent = CartBuyerAgent(
                base_url="http://testserver",
                transport=httpx.ASGITransport(app=app),
                personality=personality,
                callback=buyer_callback,
                curated_ids=curated_list,
            )
            asyncio.run(buyer_agent.run())

        thread = threading.Thread(target=run_dual_agents, daemon=True)
        thread.start()

        return {
            "status": "started",
            "message": f"Dual agents running: buyer ({style}) + merchant (LinUCB + LLM reasoner)",
            "personality": {
                "interests": interest_list,
                "avoid": avoid_list,
                "budget": budget,
                "min_discount": min_discount,
                "impulsiveness": impulsiveness,
                "patience": patience,
            },
        }

    @app.get("/products")
    def product_feed() -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": p.id,
                    "title": p.title,
                    "category": p.category,
                    "unit_amount": p.unit_amount_paise,
                    "image_url": p.image_url,
                    "description": p.description,
                    "rating": p.rating,
                    "reviews": p.reviews,
                    "stock": p.stock,
                    "tags": list(p.tags),
                    "currency": "inr",
                }
                for p in catalog
            ]
        }

    @app.post("/api/shop/greet")
    async def api_shop_greet(request: Request) -> dict[str, Any]:
        body = await _json(request)
        interests = body.get("interests", [])
        budget = body.get("budget", 10000)
        style = body.get("style", "analytical")

        from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
        from razorpay_agent.shop.assistant import ShopAssistantAgent
        regimen_graph = CoPurchaseGraph.from_catalog(catalog)
        assistant = ShopAssistantAgent(catalog=catalog, regimen_graph=regimen_graph)

        greeting = await asyncio.get_event_loop().run_in_executor(
            None, assistant.greet_and_recommend, interests, budget * 100, style,
        )

        _event_bus.put_nowait({
            "type": "shop_greeting",
            "agent": "ShopAssistant",
            "message": greeting.greeting,
            "recommendations_count": len(greeting.recommendations),
            "timestamp": datetime.now(UTC).isoformat(),
        })

        return {
            "greeting": greeting.greeting,
            "reasoning": greeting.reasoning,
            "recommendations": [
                {
                    "id": r.product.id,
                    "title": r.product.title,
                    "category": r.product.category,
                    "unit_amount": r.product.unit_amount_paise,
                    "image_url": r.product.image_url,
                    "description": r.product.description,
                    "rating": r.product.rating,
                    "reviews": r.product.reviews,
                    "stock": r.product.stock,
                    "tags": list(r.product.tags),
                    "price_paise": r.product.unit_amount_paise,
                    "price_inr": r.product.unit_amount_paise / 100,
                    "reason": r.reason,
                    "is_complement": r.is_complement,
                    "urgency_note": r.urgency_note,
                }
                for r in greeting.recommendations
            ],
            "opening_offer": greeting.opening_offer,
        }

    @app.get("/api/buyer-messages")
    def api_buyer_messages(limit: int = 50) -> dict[str, Any]:
        try:
            limit = min(max(limit, 1), 100)
        except Exception:
            limit = 50
        messages = _get_buyer_messages(limit=limit)
        return {"messages": messages}

    @app.get("/api/stream")
    async def api_stream(request: Request):
        async def event_generator():
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = _event_bus.get_nowait()
                    yield f"data: {json.dumps(event)}\n\n"
                except Exception:
                    pass
                await asyncio.sleep(0.3)
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post("/checkout_sessions/{session_id}/create-payment-link")
    async def create_payment_link(session_id: str) -> dict[str, Any]:
        state = _require_session(repository, session_id)
        if state.status != STATUS_READY:
            raise HTTPException(409, f"session is {state.status}")

        base_total = _base_total_paise(state.items, catalog)
        discount_total = _discount_total(state)
        bundle_total = _bundle_total(state)
        amount_paise = base_total - discount_total + bundle_total

        if amount_paise <= 0:
            raise HTTPException(400, "cart total must be positive")

        try:
            link = payment_provider.create_payment_link(
                amount_paise=amount_paise,
                currency="INR",
                description=f"General Goods Co. — {len(state.items)} items",
                reference=session_id[:40],
            )

            # Store order_id in session so webhook can find it
            state.order = {
                "id": link["id"],
                "checkout_session_id": session_id,
                "status": "created",
            }
            repository.save(state)

            # Calculate final amount after discount
            discount_paise = _discount_total(state)
            bundle_paise = _bundle_total(state)
            final_amount_paise = base_total - discount_paise + bundle_paise
            
            # Build items JSON for checkout URL
            from urllib.parse import quote
            items_data = []
            for entry in state.items:
                product = find_product(catalog, entry["product_id"])
                if product:
                    items_data.append({"title": product.title, "price": product.unit_amount_paise})
            items_json = quote(json.dumps(items_data))
            key_id_str = payment_provider._key_id if hasattr(payment_provider, '_key_id') else ''
            checkout_url = f"/checkout?order_id={link['id']}&key_id={key_id_str}&amount={base_total}&final_amount={final_amount_paise}&items={items_json}"

            _event_bus.put_nowait({
                "type": "payment_link_created",
                "agent": "MerchantAgent",
                "message": f"🔗 Payment link created for {len(state.items)} items — ₹{amount_paise/100:.2f}",
                "link": {
                    "id": link["id"],
                    "url": checkout_url,
                    "status": link["status"],
                    "amount_paise": amount_paise,
                    "session_id": session_id,
                },
                "timestamp": datetime.now(UTC).isoformat(),
            })

            return {
                "id": link["id"],
                "url": checkout_url,
                "razorpay_url": link["url"],
                "key_id": key_id_str,
                "status": link["status"],
                "amount_paise": amount_paise,
                "session_id": session_id,
            }
        except Exception as exc:
            _event_bus.put_nowait({
                "type": "payment_link_failed",
                "agent": "MerchantAgent",
                "message": f"Payment link creation failed: {exc}",
                "session_id": session_id,
                "timestamp": datetime.now(UTC).isoformat(),
            })
            raise HTTPException(500, f"Failed to create payment link: {exc}")

    @app.post("/api/simulate-payment")
    async def simulate_payment(request: Request) -> dict[str, Any]:
        """Simulate buyer paying through a Razorpay payment link (test-mode only).
        
        In demo mode, this immediately completes the payment without requiring
        human interaction. This uses Razorpay's official test mode API.
        """
        body = await _json(request)
        link_id = body.get("link_id")
        session_id = body.get("session_id")

        if not link_id:
            raise HTTPException(400, "link_id is required")

        state = _require_session(repository, session_id)

        # Demo mode: immediately complete payment
        try:
            success = True
            
            if success:
                base_total = _base_total_paise(state.items, catalog)
                discount_total = _discount_total(state)
                bundle_total = _bundle_total(state)
                charge_amount = base_total - discount_total + bundle_total

                state.status = STATUS_COMPLETED
                state.order = {
                    "id": link_id,
                    "checkout_session_id": state.id,
                    "payment_link_id": link_id,
                    "amount_paid_paise": charge_amount,
                    "status": "paid",
                }
                pipeline.resolve_accepted(state, charge_amount, base_total)
                state.applied_offer = None
                repository.save(state)

                _event_bus.put_nowait({
                    "type": "payment_completed",
                    "agent": "BuyerAgent",
                    "message": f"✅ Payment completed — ₹{charge_amount/100:.2f} paid for {len(state.items)} items",
                    "session_id": session_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                })

                return {"paid": True, "session_id": session_id, "amount_paise": charge_amount}
            else:
                return {"paid": False, "error": "Payment not confirmed within timeout"}

        except Exception as exc:
            raise HTTPException(500, f"Payment simulation failed: {exc}")

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
                    problems.append(_message("error", "out_of_stock", "$.items", "insufficient inventory"))
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
                    state.messages = [_message("error", "out_of_stock", "$.items", "insufficient inventory")]
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
        state.messages = [_message("error", "payment_declined", "$.payment_data", reason.title())]
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

    # ── Webhook endpoint for Razorpay payment notifications ─────────────────────

    @app.post("/api/webhook/razorpay")
    async def razorpay_webhook(request: Request) -> dict[str, Any]:
        """Handle Razorpay payment webhooks."""
        body = await _json(request)

        event = body.get("event", "")
        payload_data = body.get("payload", {})
        payment = payload_data.get("payment", {})
        entity = payment.get("entity", {})

        order_id = entity.get("order_id")
        payment_id = entity.get("id")
        status = entity.get("status")
        amount_paise = entity.get("amount", 0)

        if not order_id:
            return {"status": "ignored", "reason": "no order_id"}

        session_state = None
        session_id_found = None
        for sid, state in repository._sessions.items():
            if state.order and state.order.get("id") == order_id:
                session_state = state
                session_id_found = sid
                break

        if session_state is None:
            return {"status": "ignored", "reason": "session not found", "order_id": order_id}

        if event == "payment.captured" or status == "captured":
            session_state.status = STATUS_COMPLETED
            session_state.order = {
                "id": order_id,
                "payment_id": payment_id,
                "checkout_session_id": session_id_found,
                "amount_paid_paise": amount_paise,
                "status": "paid",
            }
            pipeline.resolve_accepted(session_state, amount_paise, _base_total_paise(session_state.items, catalog))
            session_state.applied_offer = None
            repository.save(session_state)

            _signal_payment_event(session_id_found)

            _event_bus.put_nowait({
                "type": "payment_completed",
                "agent": "BuyerAgent",
                "message": f"✅ Payment completed — ₹{amount_paise/100:.2f} paid via Razorpay",
                "session_id": session_id_found,
                "timestamp": datetime.now(UTC).isoformat(),
            })

            return {"status": "success", "session_id": session_id_found, "payment": "captured"}

        elif event == "payment.failed" or status == "failed":
            session_state.status = STATUS_NOT_READY
            session_state.messages = [_message("error", "payment_declined", "$", "Payment failed")]
            repository.save(session_state)

            _signal_payment_event(session_id_found)

            _event_bus.put_nowait({
                "type": "payment_failed",
                "agent": "BuyerAgent",
                "message": "❌ Payment failed",
                "session_id": session_id_found,
                "timestamp": datetime.now(UTC).isoformat(),
            })

            return {"status": "success", "session_id": session_id_found, "payment": "failed"}

        else:
            return {"status": "ignored", "event": event, "payment_status": status}

    @app.get("/api/webhook/status/{session_id}")
    async def webhook_status(session_id: str) -> dict[str, Any]:
        """Check if a webhook has been received for a session."""
        state = _require_session(repository, session_id)
        return {
            "session_id": session_id,
            "status": state.status,
            "order": state.order,
        }

    return app


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


def _apply_items(state: CheckoutSessionState, catalog: tuple[Product, ...], raw_items: Any) -> str | None:
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
        base_total += base
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

    discount_total = _discount_total(state)
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

    if state.reasoning_trace is not None:
        payload["reasoning"] = state.reasoning_trace

    if state.buyer_transcript is not None:
        payload["buyer_transcript"] = state.buyer_transcript

    return payload
