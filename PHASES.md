# PHASES.md — razorpay-agent build phases

**Purpose:** single cross-session context file. Read this first at the start of every
session to regain lost context without re-reading long transcripts.

**Repo:** `/home/lakshaya/projects/razorpay-agent` (Python, FastAPI, LangGraph)
**Buildathon:** Razorpay AI Builders' Buildathon 2026 — Track 01: AI Growth & Agentic Commerce
**Deadline:** ~September 5, 2026
**Demo merchant:** *General Goods Co.* (fictional — not affiliated with any real brand)

---

## Current status

| Phase | Description | Status |
|---|---|---|
| P0 | Foundation + warm-start bandit | **done** |
| P1 | Transactability + rigor proof | **done** |
| P2 | MerchantAgent / reasoning / LangGraph orchestration | **done** |
| P3 | Regimen graph (co-purchase prior + candidate-generator) | **done** |
| P4 | Live LLM reasoner providers (Nous/Tencent/OpenAI/Anthropic) | **done** |
| P5 | Docs rewrite + General Goods Co. storefront (live agent panel) | **done** |
| P6 | BuyerAgent MVP: LLM reasoning loop, memory, auto-pay + human-in-loop | **done** |
| P7 | Shop assistant (greet + curate), autonomous buyer, storefront UX | **done** |
| **P8** | **Production UI revamp + React frontend + anti-slop lint** | **done** |
| P9 | Stretch: eval extension, second merchant, settlement live | not started |

---

## P0 — Foundation + warm-start bandit

- Core contract (frozen): `ProposedAction` / `GateDecision` / `AuditEntry` (`core/`).
- Rule & policy gate: 15% + ₹300 discount cap, 20% bundle/upsell cap, one offer per
  checkout, buyer-agent allowance check. Rule layer always wins.
- Decision layer: LinUCB contextual bandit, strictly advisory.
- Warm-start snapshot `demo/pretrained_bandit.json` (5,000 synthetic pretrain episodes
  through the real gate); `run_server.py` warm-starts from it.
- Repo: `core/`, `gate/`, `decision/linucb.py`, `audit/`, `demo/pretrain_bandit.py`.

## P1 — Transactability + rigor proof

- ACP checkout surface (`checkout/api.py`) implementing the Agentic Commerce Protocol.
- ACP-speaking buyer-agent (`buyer/agent.py`): discover → session → offer review → complete.
- Live Razorpay test-mode settlement (order creation, payment link, capture) — demo Phases B/D.
- Safety watchdog: sabotage → auto-demotion → manual re-promotion.
- Eval rigor: gate fuzzing (10 invariants × 2,000 cases) + off-policy IPS counterfactual.
- Repo: `checkout/`, `buyer/`, `watchdog/`, `eval/`, `tests/test_gate_fuzzing.py`,
  `tests/test_offpolicy.py`.

## P2 — MerchantAgent / reasoning / LangGraph orchestration

- Dual-agent architecture: MerchantAgent + BuyerAgent, each Hermes-style.
- LangGraph `StateGraph` top-level orchestration: BuyerAgent → ACP → MerchantAgent →
  Gate → Razorpay → Audit.
- MerchantAgent's own decision flow is a `StateGraph` (`graph/merchant_graph.py`) with
  nodes: `build_context → generate_candidates → consult_bandit → apply_gate → finalize_offer`.
- Hermes-style reasoner (`reasoning/agent.py`): AIAgent loop (prompt → API → tool_calls →
  loop → persist, `MAX_REASONING_STEPS` budget), self-registering `ToolRegistry` with
  `check_fn` availability gating + error wrapping.
- Reasoning tools (`reasoning/tools.py`): `get_catalog_item`, `get_clearance_policy`,
  `get_bandit_scores`, `estimate_outcome`, `get_regimen_graph` — read-only, arg schemas.
- Storefront: thin React page (`storefront/index.html`) with agent/human browsing badge.
- Repo: `graph/`, `reasoning/`, `storefront/`, `merchant.py`.

## P3 — Regimen graph (co-purchase prior)

- `decision/co_purchase_graph.py`: MerchantAgent graph state holding regimen/co-purchase
  relationships as a **documented prior** (edge weight = regimen strength, degree = popularity).
- `candidate_bundles_for(...)`: the **candidate-generator node** — given a target SKU,
  returns regimen-anchored `BundleArm`s (each with `anchor_sku == target_sku`).
- `BundleArm.anchor_sku` added (regimen-anchored bundles; static catalog bundles leave
  it `None`). Backward-compatible serialization.
- `eval/replay.py` simulator **honors the prior**: bundle relevance derived from
  `CoPurchaseGraph.relevant_categories(...)`. Reward formula reuses
  `BUNDLE_RELEVANT/IRRELEVANT_TAKE_RATE`.
- Tests: `tests/test_co_purchase.py`.

## P4 — Live LLM reasoner providers

- Live LLM providers inside the *isolated, advisory* `reasoning/` module only.
- `reasoning/llm.py`: `StubBackend` (keyless default), `OpenAIBackend`, `AnthropicBackend`,
  `TencentBackend` (Tencent HY3), `NousBackend` (Nous Portal) — all OpenAI-compatible.
- Provider resolution: explicit → config → `RAZORPAY_AGENT_LLM_PROVIDER` env → `stub`.
  Any failure degrades to `stub`; demo always runs keyless.
- Scoped `.env` loader exports only LLM keys; Razorpay creds isolated in server loader.
- `openai` is an **optional** dependency (`[llm]` extra); core install stays LLM-free.
- Tests: `tests/test_reasoning_llm.py`.

---

## P5 — Docs rewrite + General Goods Co. storefront

**Goal:** Rebrand from "Plain Goods Co." to "General Goods Co." and build the live
agent-interaction storefront for the 5-minute pitch video. Rewrite all docs around the
enveloped-LLM, Hermes-style, dual-agent thesis.

### Workstream A: Storefront
- [x] A1: Rebrand (`merchant.py`, `catalog.py`, `storefront/index.html`) + curate new D2C
      catalog (more products, realistic prices, strong regimen relationships). **done**
- [x] A2: Live agent panel — full side-by-side view: buyer agent transcript, merchant
      reasoning trace, gate decision, audit entry — all updating live via polling. **done**
- [x] A3: `/storefront/events` endpoint for streaming recent audit entries + reasoning traces. **done**
- [x] A4: Wire up `server.py` — serve new page, expose events endpoint. **done**

### Workstream B: Docs rewrites
- [x] B1: `architecture.md` — §2 principle 1 (dual-agent + enveloped-LLM), §4.4 (BuyerAgent
      reframed), §5 (LangGraph flow), §4.10 (Hermes patterns). **done**
- [x] B2: `prompt.md` — verify/update non-negotiable constraints. **done**
- [x] B3: `README.md` — dual-agent, LangGraph, LLM reasoner, General Goods Co. **done**
- [x] B4: `PITCH.md` — full rewrite around new thesis + live storefront shot + competitive positioning. **done**

---

## P6 — BuyerAgent MVP: LLM reasoning loop, memory

**Goal:** Upgrade the BuyerAgent from a deterministic threshold policy to a genuine
LLM-powered agent — matching the merchant's Hermes-style reasoner on the buyer side.
The buyer agent has memory, evaluates offers with clear criteria, and writes its own
reasoning trace. Counter-offers removed: buyer accepts or declines; the purchase
always happens regardless.

### Workstream A: Buyer reasoning loop — **done**
- [x] A1: `buyer/reasoning_agent.py` — single-pass LLM call with clear acceptance criteria
      (discount % vs minimum, add-on share vs max), strict verdict format.
- [x] A2: No separate tools file needed — all context passed directly in the prompt
      (budget, memory, cart value already in the session payload).
- [x] A3: `buyer/agent.py` — updated to accept an `llm` parameter, uses `resolve_provider()`
      for keyless default.
- [x] A4: Removed `buyer_reasoning_log` side table — buyer transcript lives in the
      `PurchaseMemory` side table (simpler, sufficient for MVP).

### Workstream B: Memory — **done**
- [x] B1: `PurchaseMemory` class (`buyer/reasoning_agent.py`) — tracks resolved purchases
      (SKU, category, price, timestamp); used by the reasoner to avoid repeat buys.
- [x] B2: Memory passed through `BuyerAgent.__init__`; serialized to JSON for demo.

### Workstream C: Demos + tests — **done**
- [x] C1: `tests/test_buyer_agent.py` — full end-to-end ACP flow, reasoning verdict tests,
      mandate/expiry/cancel invariants.
- [x] C2: `tests/test_reasoning_quality.py` — strict verdict format verification.
- [x] C3: `demo/pretrain_reasoner.py` — pretrains both merchant AND buyer reasoners.

### Workstream D: Autonomous buyer + auto-pay/human-in-loop — **done**
- [x] D1: `buyer/autonomous_agent.py` — `CartBuyerAgent` with full browse → cart → checkout → payment flow.
- [x] D2: `Personality.demo_auto_pay` flag — when True, agent auto-pays via test API (no human needed).
- [x] D3: Payment link visibility logic — auto-pay hides link; human-in-loop shows it.
- [x] D4: `POST /api/autonomous/start` accepts `demo_auto_pay` parameter.
- [x] D5: Storefront toggle for auto-pay vs human-in-the-loop modes.

### Workstream E: Multi-tool buyer harness — **done**
- [x] E1: `buyer/tools.py` — 7 read-only tools: catalog, budget, purchase history, interests, offer evaluation, affordability, cart total.
- [x] E2: `buyer/llm_response.py` — structured `LLMResponse` with `ToolCall` + `Verdict`, parses both `<<tool>>` and Nous `<tool_call>` formats.
- [x] E3: `buyer/autonomous_agent.py` — multi-step tool-calling loop (up to 4 reasoning steps per item), 30s timeout with skip fallback.
- [x] E4: Real LLM by default (10s test timeout), stub fallback on failure.
- [x] E5: Removed all heuristic paths — passive style uses LLM like other styles.

---

## P7 — Shop assistant, autonomous buyer, storefront UX

**Goal:** Add a shop assistant that greets buyers and curates products by interest.
Build the autonomous buyer flow with full storefront integration.

### Workstream A: Shop assistant — **done**
- [x] A1: `shop/assistant.py` — `ShopAssistantAgent` with `greet_and_recommend()` method.
- [x] A2: Uses `CoPurchaseGraph` for regimen-aware recommendations.
- [x] A3: `POST /api/shop/greet` endpoint — accepts interests, budget, style; returns greeting + recommendations.

### Workstream B: Storefront integration — **done**
- [x] B1: `storefront/index.html` — full dual-agent UI with buyer/merchant panels, product grid, cart.
- [x] B2: SSE event stream (`/api/stream`) for live reasoning traces.
- [x] B3: `/api/buyer-messages` endpoint for polling buyer reasoning.
- [x] B4: Phase-based UI (Discovery → Browsing → Negotiation → Checkout → Done).
- [x] B5: Auto-pay vs human-in-the-loop toggle in UI.

### Workstream C: Payment link failure mode — **done**
- [x] C1: `fail_link_creation` flag in `ScriptedPaymentProvider` and `RazorpayTestProvider`.
- [x] C2: `payment_link_failed` SSE event for graceful failure display.
- [x] C3: Storefront shows failure state with retry context.

---

## P8 — Production UI revamp + product images (in progress)

**Goal:** Transform the storefront from a demo-like interface into a polished, production-quality
shopping experience. Add product images so the buyer agent (and human viewers) can see what
they're buying. Live reasoning trace panel shows the AI's thought process.

### Workstream A: Product images — **in progress**
- [ ] A1: Generate/source product images (placehold.co or similar with SKU-specific colors).
- [ ] A2: Add `image_url` field to catalog products.
- [ ] A3: Serve images via CDN or local placeholder service.
- [ ] A4: Wire images into buyer agent prompt (vision support) or text description fallback.

### Workstream B: Storefront UI revamp — **in progress**
- [ ] B1: Redesign as modern D2C storefront (Stripe/Linear/Vercel aesthetic).
- [ ] B2: Product cards with images, hover effects, category badges.
- [ ] B3: Sticky header with cart, search, agent mode toggle.
- [ ] B4: Reasoning trace panel (collapsible) showing live LLM thought process.
- [ ] B5: Responsive layout, smooth animations, dark/light mode.

### Workstream C: Live reasoning trace — **pending**
- [ ] C1: Show tool calls in real-time (e.g., "🔧 checking budget... → ₹10000 remaining").
- [ ] C2: Show LLM reasoning text (truncated to 1-2 sentences).
- [ ] C3: Animated thinking indicators (typing dots, pulse effects).

---

## Locked architecture decisions (frozen, do not bend)

- **Core contract:** `ProposedAction` / `GateDecision` / `AuditEntry` — add fields only via
  new module, never by bending these.
- **Rule layer always wins** over the decision layer; watchdog demotes on drift.
- **LLM reasons only; never calls settlement.** Money execution stays non-LLM.
- **StubBackend default** ⇒ demo runs keyless; provider resolver scopes keys per base_url.
- **No LangGraph required for the bar** — the no-LLM path (bandit + gate + StubBackend)
  remains a working safety net that clears the buildathon bar on its own.

## Key files map

| Concern | Path |
|---|---|
| Core contract | `src/razorpay_agent/core/` (actions, decisions, audit, currency, errors) |
| Rule gate | `src/razorpay_agent/gate/` (gate, context, limits) |
| Decision layer | `src/razorpay_agent/decision/` (linucb, arms, context, co_purchase_graph) |
| Checkout surface | `src/razorpay_agent/checkout/` (api, catalog, inventory, offers, payments, sessions) |
| Buyer agent | `src/razorpay_agent/buyer/agent.py` |
| Autonomous buyer | `src/razorpay_agent/buyer/autonomous_agent.py` |
| Buyer tools | `src/razorpay_agent/buyer/tools.py` |
| Buyer LLM response | `src/razorpay_agent/buyer/llm_response.py` |
| Audit trail | `src/razorpay_agent/audit/store.py` |
| Eval harness | `src/razorpay_agent/eval/` (charts, offpolicy, replay, report, storage, synthetic) |
| Watchdog | `src/razorpay_agent/watchdog/` (watchdog, sabotage, storage) |
| LangGraph | `src/razorpay_agent/graph/merchant_graph.py` |
| LLM reasoner | `src/razorpay_agent/reasoning/` (agent, llm, store, tools) |
| Storefront | `src/razorpay_agent/storefront/index.html` |
| Shop assistant | `src/razorpay_agent/shop/assistant.py` |
| Server entry | `src/razorpay_agent/server.py` |
| Merchant branding | `src/razorpay_agent/merchant.py` |
| Demos | `demo/` (pretrain, run_full_demo, run_watchdog_demo, run_offpolicy_demo, run_reasoning_demo) |
| Architecture doc | `architecture.md` |
| Build rules | `prompt.md` |
| Pitch script | `PITCH.md` |
