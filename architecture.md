# razorpay-agent — Architecture

**Project:** razorpay-agent
**Context:** Razorpay AI Builders' Buildathon (fellowship track)
**Deadline:** ~September 5, 2026
**Built by:** solo developer
**Demo merchant:** *Common* is a **fictional** merchant invented for this buildathon. The catalog, regimen graph, and storefront are all invented; no real brand is depicted or implied.

This document is the single source of truth for the architecture of razorpay-agent. It exists so that both a human and an LLM coding agent can pick it up cold and understand not just *what* to build, but *why* every structural decision was made — so that future changes stay consistent with the reasoning instead of drifting from it.

---

## 1. Problem Statement

The buildathon brief asks for an agent that either (a) grows revenue for a merchant on Razorpay's test-mode APIs, or (b) makes a merchant transactable end-to-end by an AI buyer — judged against the bar that **every money action must be explainable, bounded, and gated**, with a visible **audit trail**, and **one failure handled gracefully**.

razorpay-agent does both (a) and (b) in one coherent system: a merchant-side decisioning agent that grows revenue by proposing discounts and bundle upsells, hosted behind a real agent-to-agent commerce protocol (ACP) so that an actual AI buyer-agent can discover the merchant, transact, and receive those offers end-to-end.

## 2. Non-Negotiable Design Principles

These three principles were decided explicitly and constrain every downstream choice in this document. Any future change must be checked against them.

1. **Two agents, one gated money path — the LLM advises, never executes.** The system is a dual-agent architecture: a **MerchantAgent** (proposes offers via a LinUCB bandit + optional Hermes-style LLM reasoner) and a **BuyerAgent** (discovers the catalog, negotiates over ACP, accepts/declines independently). Both are Hermes-style — each with its own harness, eval, and graph state — but the money path is structurally non-LLM: the LLM in `reasoning/` explains *why* an offer was proposed through **read-only** tools, writes only to a `reasoning_log` side table, and never proposes or executes settlement. Every money action flows through the rule & policy gate (principle 2: the rule layer always wins), the immutable core contract, and the audit trail. If the LLM is missing, misconfigured, or fails, it degrades to a keyless `StubBackend` with zero effect on any decision. The bandit and the eval harness remain deliberately non-LLM; the reasoning layer is the one explicitly LLM-backed component. Orchestration is via LangGraph: BuyerAgent → ACP negotiate → MerchantAgent → Gate → Razorpay settle → Audit.
2. **Minimal dependency on any single model.** The system must remain safe and functional even if its learned model (the bandit) were replaced, disabled, or wrong. This is achieved structurally, not by trusting the model to behave — see the Core Contract below.
3. **A small, stable core with pluggable edges.** The core is deliberately tiny (three data shapes and one rule). Everything else is a replaceable module that speaks to that core. This is what allows components to be added, removed, or swapped later without destabilizing the rest of the system.

## 3. The Core Contract (the immutable spine)

The core of razorpay-agent is not a piece of code — it's a contract: three fixed data shapes that every other component reads or writes, and one rule that governs how they interact. **This is the part of the system that should almost never change**, because everything else is built to depend on it staying stable.

### 3.1 ProposedAction

Emitted by anything that wants to suggest a money-affecting action (currently: the decision layer, or the rule layer's own fallback). Never executed directly — always passed to a gate first.

| Field | Meaning |
|---|---|
| `action_type` | `"discount"` or `"bundle_upsell"` |
| `target` | Which cart item the action applies to (discount) or which item is being suggested for addition (bundle) |
| `discount_percent` | Present only for `discount` actions |
| `bundle_item`, `bundle_price` | Present only for `bundle_upsell` actions |
| `expected_uplift` | The proposing module's own estimate of revenue impact |
| `confidence` | A number from 0 to 1 — how sure the proposing module is |
| `source` | Which module produced this (e.g. `"linucb_bandit"` or `"fallback_rule"`) — critical for audit clarity |
| `session_id` | The checkout session this proposal belongs to |

### 3.2 GateDecision

Emitted by the rule & policy layer in response to a `ProposedAction`. This is the only thing in the system that is allowed to turn a proposal into a real action.

| Field | Meaning |
|---|---|
| `allowed` | `true` / `false` |
| `checked_against` | Which specific limits were evaluated (e.g. `["max_discount_pct", "buyer_allowance"]`) |
| `reason` | Human-readable explanation of the decision |
| `final_action` | The action that actually goes out — may equal the original proposal, a capped-down version of it, or the fallback default if rejected |

### 3.3 AuditEntry

Written for **every** proposal, regardless of outcome. This is what gets shown in the demo to satisfy the "show the audit trail" requirement.

| Field | Meaning |
|---|---|
| `timestamp` | When this happened |
| `session_id` | Which checkout session |
| `proposed_action` | The full `ProposedAction` object |
| `gate_decision` | The full `GateDecision` object |
| `outcome` | What actually happened afterward: `offered` (provisional — recorded the moment the gate allows a proposal, before buyer resolution), then finalized to `accepted` / `declined` / `failed` at session resolution, with detail (e.g. failure reason) |

### 3.4 The One Rule

**Nothing acts unless it has passed through the gate. Nothing that happens goes unrecorded.**

This single rule is what makes the "explainable, bounded, gated" bar concrete rather than a slogan, and it's also what makes the model-dependency principle real: the decision layer's output is never trusted directly, so a wrong, missing, or replaced model degrades quality of suggestions, never the safety of the system.

---

## 4. Components

Each component below is a module that speaks to the core contract. None of them need to know how the others are implemented internally — only what shape of data comes in and goes out.

### 4.1 Rule & Policy Layer

The deterministic safety layer. Always wins over the decision layer. Encodes a small, fixed set of hard limits:

- **Max discount:** 12–15%, paired with an absolute rupee cap (not just a percentage — mirrors how Razorpay's own live Offers product structures discounts, e.g. "10% off, capped at ₹300").
- **Max bundle/upsell price:** capped at roughly 20% of the existing cart value, so a suggested add-on stays proportionate rather than derailing the checkout.
- **One offer per checkout session.** No stacking, no repeated prompts.
- **Buyer-agent's spending allowance:** any proposal that would push the session total past what the buyer-agent's payment mandate authorizes is rejected regardless of the other checks passing.

If a `ProposedAction` is rejected, the gate's `final_action` falls back to a plain default (e.g. "no offer" or a static most-commonly-bundled item), so the checkout always completes safely even with no learned model involved at all.

Concretely, in this implementation: the plain default is the static most-commonly-bundled item, emitted as a valid `ProposedAction` with source `"fallback_rule"`, its price held within the bundle share limit by construction. A rejected decision carries this fallback as its `final_action` for audit completeness; the checkout surface renders an offer only for decisions with `allowed: true`. Discount proposals that exceed a limit are capped down where capping produces a still-meaningful offer, and rejected otherwise; allowance breaches are always outright rejections, never capped.

### 4.2 Decision Layer — LinUCB Contextual Bandit

The only component in the system allowed to be a learned model, and even then, it is strictly advisory — its output is a `ProposedAction`, nothing more.

- **Algorithm:** LinUCB (a contextual bandit). Chosen over epsilon-greedy for smarter, confidence-weighted exploration — it converges faster, which matters because the live demo will only produce a handful of real transactions to learn from.
- **Context (what it sees):** cart contents, item category, cart value, and the buyer-agent's stated spending allowance. Deliberately kept small and legible — more context means harder-to-explain individual decisions.
- **Action space — discrete arms:** the bandit chooses among a small fixed menu of candidate offers: a handful of configured discount percentages plus cataloged bundle items with fixed prices. Continuous-percentage scoring was considered and rejected: regret accounting for the eval harness is only clean over finite arms, every proposal stays directly explainable ("the 10% arm"), and a fixed menu makes policy leakage structurally hard — the bandit selects among candidates rather than optimizing a value it might be tempted to clamp. Arms deliberately span offers the gate may cap down or reject; the bandit never adjusts a proposal to fit policy, it just learns from what happens to each arm. Bundle arms optionally carry an `anchor_sku` naming the cart item they are paired to (regimen-anchored bundles set it; static catalog bundles leave it unset) — see §4.9.

### 4.3 Reasoning Layer (Advisory Only)

Hermes-style advisory reasoners for both agents — strictly read-only, never touches the money path:

- `reasoning/agent.py`: AIAgent loop (prompt → API → tool_calls → loop → persist), self-registering `ToolRegistry`.
- `reasoning/llm.py`: provider resolution with `StubBackend` fallback — any failure degrades to keyless stub.
- Tools: `get_catalog_item`, `get_clearance_policy`, `get_bandit_scores`, `estimate_outcome`, `get_regimen_graph`.

### 4.4 BuyerAgent

ACP-speaking buyer agent with its own LLM reasoner:

- Discovers catalog, opens session, evaluates offers, accepts/declines.
- Memory: purchase history for repeat-buyer behavior.
- Verdict format: `Verdict: ACCEPT` / `Verdict: DECLINE`.

### 4.5 Shop Assistant

Greets the buyer, curates recommendations based on interests and budget. Entry point: `POST /api/shop/greet`.

### 4.6 Settlement

Razorpay test-mode settlement: order creation, payment link, capture. Falls back to scripted provider when creds absent.

### 4.7 Frontend (React + TypeScript)

Production-grade storefront in `web/`:

- **Stack:** Vite + React 18 + TypeScript, pure CSS (no UI libs).
- **Design:** YC-themed — orange (#FF4000) + black + white editorial aesthetic.
- **Features:** live agent panel, product grid with filters, cart, product modal, reasoning log.
- **Build:** `npm run build` → `dist/`, served by FastAPI at `GET /storefront`.
- **Lint:** Oxlint + anti-slop plugin (15 generic rules).

### 4.8 Eval & Watchdog

- Gate fuzzing: 20,000 decisions, 0 violations.
- Off-policy IPS counterfactual with 95% CI.
- Watchdog: sabotage detection → auto-demotion → manual re-promotion.

### 4.9 Regimen Graph

`decision/co_purchase_graph.py`: co-purchase prior as documented edge weights. Bundle arms carry `anchor_sku` for regimen-anchored suggestions.

---

## 5. Repository Layout

```
razorpay-agent/
├── src/razorpay_agent/
│   ├── core/               # ProposedAction, GateDecision, AuditEntry
│   ├── gate/               # Rule & policy layer
│   ├── decision/           # LinUCB bandit + regimen graph
│   ├── checkout/           # ACP API + Razorpay settlement
│   ├── buyer/              # BuyerAgent
│   ├── reasoning/          # Advisory LLM reasoners
│   ├── shop/               # Shop assistant
│   ├── merchant.py         # MerchantAgent graph
│   ├── server.py           # FastAPI app factory
│   ├── storefront/         # Static HTML fallback
│   └── eval/               # Eval harness + watchdog
├── web/                    # React frontend (Vite + TS)
│   ├── src/
│   │   ├── components/     # Header, Hero, ProductCard, CartPanel, etc.
│   │   ├── context/        # AppContext (state + startDemo)
│   │   ├── types/          # Product, CartItem, AppState
│   │   └── index.css       # YC-themed design system
│   ├── tools/oxlint/       # anti-slop lint plugin
│   └── dist/               # Production build (served by FastAPI)
├── demo/                   # Demo scripts + pretrain
├── tests/                  # Pytest suite
├── architecture.md         # This file
├── prompt.md               # How to change the system
└── README.md               # Quickstart
```

---

## 6. Running in Production

```bash
# Backend
pip install -e ".[dev,llm]"
python run_server.py          # :8613

# Frontend (development)
cd web && npm install && npm run dev    # :5173

# Frontend (production build)
cd web && npm run build       # outputs to web/dist/
# FastAPI auto-serves dist/index.html at GET /storefront

# Full e2e demo
python demo/run_full_demo.py --wait 900
```

Environment variables:
- `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` — live Razorpay (optional, falls back to scripted)
- `RAZORPAY_AGENT_LLM_PROVIDER` — `stub` (default), `openai`, `anthropic`, `nous`

---

## 7. What to Change (and What Not To)

**Frozen (don't break):** the core contract (§3), the one rule (§3.4), the gate's hard limits.

**Pluggable (safe to swap):** the bandit algorithm, the LLM provider, the frontend design, the catalog content.

See `prompt.md` for the full change-governance rules.
