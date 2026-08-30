# razorpay-agent — Architecture

**Project:** razorpay-agent
**Context:** Razorpay AI Builders' Buildathon (fellowship track)
**Deadline:** ~September 5, 2026
**Built by:** solo developer

This document is the single source of truth for the architecture of razorpay-agent. It exists so that both a human and an LLM coding agent can pick it up cold and understand not just *what* to build, but *why* every structural decision was made — so that future changes stay consistent with the reasoning instead of drifting from it.

---

## 1. Problem Statement

The buildathon brief asks for an agent that either (a) grows revenue for a merchant on Razorpay's test-mode APIs, or (b) makes a merchant transactable end-to-end by an AI buyer — judged against the bar that **every money action must be explainable, bounded, and gated**, with a visible **audit trail**, and **one failure handled gracefully**.

razorpay-agent does both (a) and (b) in one coherent system: a merchant-side decisioning agent that grows revenue by proposing discounts and bundle upsells, hosted behind a real agent-to-agent commerce protocol (ACP) so that an actual AI buyer-agent can discover the merchant, transact, and receive those offers end-to-end.

## 2. Non-Negotiable Design Principles

These three principles were decided explicitly and constrain every downstream choice in this document. Any future change must be checked against them.

1. **Zero LLM anywhere in the system.** Not in the decision layer, not in the checkout surface, not in the eval harness. The "AI" in this project is the eval harness, the bandit, and the multi-agent orchestration — not a language model. This is a deliberate response to the buildathon's own framing, which explicitly asked for heavy AI work (eval/harness/agents) rather than an LLM wrapper.
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
- **Action space — discrete arms:** the bandit chooses among a small fixed menu of candidate offers: a handful of configured discount percentages plus cataloged bundle items with fixed prices. Continuous-percentage scoring was considered and rejected: regret accounting for the eval harness is only clean over finite arms, every proposal stays directly explainable ("the 10% arm"), and a fixed menu makes policy leakage structurally hard — the bandit selects among candidates rather than optimizing a value it might be tempted to clamp. Arms deliberately span offers the gate may cap down or reject; the bandit never adjusts a proposal to fit policy, it just learns from what happens to each arm.
- **Abstention:** if even the most optimistic arm score (expected reward plus exploration bonus) is non-positive, the bandit proposes nothing for that session — low-confidence silence is a valid decision-layer output, per §5 step 2.
- **Context encoding:** the legible context maps to a small numeric vector — intercept, cart value scaled to thousands of rupees, allowance-to-cart ratio, one-hot item category. The SKU itself is deliberately not a feature; the category carries that signal.
- **Pretraining and persistence:** the policy's full learned state — per-arm `A` matrices and `b` vectors, alpha, arm definitions, encoder categories, update count — serializes to plain JSON at `demo/pretrained_bandit.json`. A one-time pretraining script (`demo/pretrain_bandit.py`) runs the bandit through 5,000 synthetic episodes via the eval harness's simulator, with every proposal passing through the real gate exactly as in production. `run_server.py` warm-starts from this file whenever it exists and keeps learning from live transactions thereafter (the hybrid strategy of §4.5); if the file is missing it starts cold with a loud log line — the same no-silent-fallback principle as the payment credentials.
- **Observed converged behavior:** after pretraining, the policy reliably prefers its best net-revenue arm (a modest discount), with near-total confidence — meaning an over-cap proposal becomes an exploration artifact rather than steady-state behavior, and the rule layer's caps bind mainly on high-value carts where even the preferred percentage exceeds the rupee ceiling. This is the intended division of labor showing up empirically: training reduces reliance on the gate; it never replaces it.
- **Reward signal:** **net revenue gained**, not raw accept/decline — the value gained from an accepted offer minus the discount cost. A policy that accepts everything by discounting to the max would score poorly here even with a high acceptance rate.
- **Classified as RL, but the simplest slice of it:** a contextual bandit is single-step reinforcement learning — one decision, one immediate reward, no modeling of how this action affects future states. This keeps it lightweight, interpretable, and appropriate for the "minimal model dependency" principle, as opposed to full multi-step RL.
- **Swappability:** because this component only ever emits a `ProposedAction`, it can be replaced by any other decision-making approach (a different bandit, a rules engine, anything) without touching the rule layer, the audit trail, or the checkout surface.

### 4.3 Checkout Surface — ACP-Compliant

The door a buyer-agent walks through. Implements the **Agentic Commerce Protocol (ACP)**, chosen over the alternatives for the following reasons (see Section 6 for the full comparison):

- **Product feed:** structured, machine-readable catalog data the buyer-agent can query.
- **Checkout session lifecycle:** `create`, `update`, `get`, `complete`, `cancel` endpoints. Every response returns the full authoritative session state.
- **Delegated payment:** the buyer-agent provides a payment credential scoped with a **maximum chargeable amount and an expiry** — not raw payment details. This scoped allowance functions as a lightweight mandate (proof of what the buyer-agent is authorized to spend) without needing to implement Google's full AP2 protocol separately — ACP's own delegated payment spec already carries this concept.
- **Integration point with the core contract:** at session creation (and update), the decision layer is invoked, its `ProposedAction` is passed through the gate (checked against the rule layer's limits **and** the buyer-agent's allowance), and the session's returned totals reflect only what the gate allowed.
- **Settlement:** the actual charge is executed via **Razorpay's test-mode APIs**, with Razorpay standing in as the payment provider in ACP's delegated payment flow.
- **Demonstrated settlement scope:** against live test-mode credentials, the full flow is demonstrated end-to-end: order creation per transaction (the real `order_...` id flows into the ACP session payload and audit trail), order-status polling straight from the Orders API, and actual capture through a Razorpay Payment Link's hosted checkout (test-mode netbanking/domestic cards), with the demo printing each state exactly and only as Razorpay's APIs report it — `created`, then `paid` once genuinely paid — and stating plainly when capture has not happened. One honest structural note: capture settles via the payment link's own Razorpay order; the merchant-side Orders API entry remains `created` until a Standard Checkout integration binds capture to that same order id. No retry ever occurs on failure, per §5.7.
- **Spec fidelity:** implemented against the published ACP specification (agentic-commerce-protocol repo, OpenAPI version 2026-04-17): exact endpoint paths (`POST /checkout_sessions`, `GET`/`POST /checkout_sessions/{id}`, `POST .../complete`, `POST .../cancel`), the official status enum (`not_ready_for_payment | ready_for_payment | completed | canceled`), integer minor-unit amounts (paise) with lowercase ISO 4217 currency (`inr`), create returning 201 with a session id even when validation fails (problems carried as `messages[]` errors), and complete returning an `order` object on success.
- **Allowance intake:** until a full delegated-payment/Shared-Payment-Token integration exists, the buyer-agent conveys its spending mandate at session creation via an `allowance` object mirroring the delegate-payment spec's fields (`max_amount` in paise, `currency`, `expires_at`). The gate checks this same allowance when evaluating proposals; completion re-verifies expiry before charging.
- **Bundle rendering:** an approved `bundle_upsell` appears in the session payload as a `suggested_add_on` field plus an informational message — a small, documented extension beyond the base ACP session shape. Discounts are reflected directly in line-item `discount` fields and totals.
- **Session storage:** checkout sessions live in an in-memory repository (ephemeral runtime state); the durable record of anything that matters is the audit trail, per §4.6.

### 4.4 Buyer-Agent (scripted, ACP-speaking)

A stand-in built specifically for this project to prove end-to-end transactability, rather than assuming an external agent exists. It genuinely speaks ACP (discovers the product feed, creates a session, receives/reviews an offer, completes with a scoped payment token) rather than using a custom ad-hoc format — this was a deliberate choice to make the "transactable by a real AI buyer" claim defensible rather than simulated in name only.

In this implementation the buyer-agent is an asynchronous HTTP client speaking real ACP against any base URL it is pointed at. Its offer review is a deterministic threshold policy — accept a discount at or above a configured minimum percent, accept a suggested add-on within a configured share of cart — and it independently re-checks its own spending mandate before completing, canceling rather than exceeding it. Every run emits a plain-language transcript of what it saw and decided, so a demo can show exactly why the buyer behaved as it did.

### 4.5 Eval Harness

The centerpiece of the project's "heavy AI work" story. Its job is to prove the bandit is better than doing nothing clever, **before** it is trusted with live proposals, and to keep validating it as it continues learning.

- **Data strategy (hybrid):** synthetic checkout data is generated first for offline validation, then the bandit continues learning from real transactions during live demo runs. Pure live-only learning was rejected because a live demo produces too few transactions for a bandit to show meaningful learning; pure synthetic-only was rejected because it would misrepresent a simulation as real-world performance.
- **Synthetic buyer model:** deliberately kept simple and legible (a probabilistic model with a small number of understandable factors — offer size, offer relevance, randomness) rather than another opaque learned model, to avoid just moving the "trust me" problem down a layer. Acceptance is probabilistic, not a hard rule, and varies by context (different simulated cart types respond differently to the same offer) so there is genuinely something for the bandit to learn.
- **Calibration:** the synthetic acceptance model is calibrated using researched benchmarks as a **design input** — a baseline upsell acceptance rate in the 3–8% range (rising with more relevant/generous offers), general ecommerce discount norms of roughly 10–30% with luxury/high-ticket items staying under 15%, and the fact that automatic (non-coupon) discounts have been reported to reduce cart abandonment by 10–20%.
- **Metrics:**
  - **Uplift over baseline** — the bandit's policy, replayed against synthetic/historical sessions, compared to the plain rule-based fallback.
  - **Gate-compliance rate** — how often the bandit's *raw* suggestions (before gating) would have passed the gate unmodified. A bandit that's constantly rejected isn't learning inside its real constraints.
  - **Regret** — a standard bandit metric: how much worse the bandit's choices were compared to the best possible choice in hindsight.
- **Honesty framing for the demo:** the offline number is evidence the bandit-plus-gate machinery works correctly and learns something coherent under controlled conditions — not a claim about real-world revenue performance.
- **Live self-reporting:** the eval numbers are exposed by the running system itself, not computed off to the side. A small `/eval/report` endpoint lives in the same FastAPI app as the checkout surface, reads from the same audit store the checkout flow writes to, and returns the three metrics above (uplift over baseline, gate-compliance rate, regret) as plain JSON. A short matplotlib script renders the demo charts (uplift over time as the bandit learns, regret trending down) from that same underlying data. This keeps the harness's verdict provably connected to the real system — same data source, same running process — so the audit trail and the harness's own assessment can be shown side by side with nothing explained away as a separate pipeline.
- **Simulator factors (implemented):** four legible mechanisms, no learned components: category-level price sensitivity scales how much a discount lifts completion; bundle take-rates split relevant (scaled by affordability) versus irrelevant (~4%, the low end of the researched 3–8% passive baseline); a pushiness factor makes oversized or irrelevant add-ons risk abandoning checkout entirely; a flat 1% annoyance probability attaches to any rendered offer. Expected net revenue per (session, action) is computed analytically and shared by both the reward path and the regret path, so they cannot drift apart.
- **Reward and regret definitions (implemented):** reward is realized net revenue measured against the session's own no-offer completion probability (the simulator knows the counterfactual, which offline evaluation legitimately allows). Regret is standard pseudo-regret: per decision, the analytic best arm including proposing nothing, minus the analytic value of what actually went out after gating — summed over time, plotted as a flattening curve.
- **Baseline:** the comparison policy is the gate's own static fallback bundle proposed through the identical gate — the honest "no learned model" alternative, evaluated on paired identical sessions.
- **Storage and serving (implemented):** replay runs persist per-step records and run summaries into an eval store backed by the same SQLite database file as the audit trail (separate tables, one data home). `/eval/report` returns the latest run's metrics plus the honesty note verbatim: evidence of coherent learning under controlled conditions, not a prediction of real-world revenue.
- **Off-policy counterfactual evaluation (implemented):** every bandit-proposed decision lands in a `decision_log` table (context fields, chosen arm, allowed-unmodified flag) and its reward — the *identical* §4.7-aligned net-revenue metric, never a second derivation — is written back at resolution. `GET /eval/offpolicy?alpha=X` then estimates what an otherwise-identical policy differing only in exploration parameter would have earned over the logged window, via self-normalized inverse propensity scoring. Because pure-argmax LinUCB has degenerate selection probabilities (0 or 1), both policies are scored under a stated uniform-exploration kernel (ε=0.05) — disclosed as the condition for IPS to be well-defined, not passed off as what literally ran. Both sides are scored statically from the pretrained snapshot (α affects only the bonus term); snapshot-vs-logged argmax agreement is computed and surfaces as a drift caveat below 70%. Every stable estimate carries a 95% confidence interval and effective sample size; fewer than 30 resolved decisions or a degenerate ESS returns an explicit too-sparse verdict instead of a number. First real window (260 logged decisions): α=0.25 estimated at ₹292.85/decision (CI 272–313, ESS 260/260, 100% snapshot agreement) — identical argmax throughout, so the honest counterfactual finding was "reduced exploration would have changed nothing on this window," which is precisely the kind of unexciting truth this machinery exists to report faithfully.

### 4.6 Audit Trail

A durable log of every `AuditEntry` ever written, queryable and displayable — this is what gets shown live to satisfy the "show the audit trail" requirement. Every entry exists regardless of whether the proposal was approved, rejected, accepted, declined, or failed.

Implemented as a SQLite store (see §11). Both the checkout surface and the eval report endpoint (§4.5) read and write through this one store — there is exactly one source of record. Writes are executed through a thread pool when issued from async request handlers, so disk latency can never stall request handling.

Entry timing: every gate-allowed proposal gets a single `AuditEntry` the moment the gate allows it, with a provisional `offered` outcome (so an approved offer is recorded even if the buyer abandons the session — there is no silent path). Proposals rejected by the gate get their `AuditEntry` immediately, with outcome `declined` naming the gate's reason. At session resolution the provisional `offered` entry is updated in place (never duplicated) to its final outcome — `accepted` on successful payment, `declined` if the buyer cancels, `failed` if settlement fails — so every entry's outcome states final truth about what happened, never a stale provisional state. Every proposal ends up with exactly one entry; nothing is dropped or silently overwritten.

### 4.7 Safety Watchdog

System-level extension of "bounded and gated": the gate bounds each transaction; the watchdog bounds the decision layer as a whole.

- **What it watches:** rolling window (last 100 bandit decisions) of two signals — net revenue per decision and raw-proposal gate-compliance rate. Abstentions count as decisions worth zero revenue but carry no compliance signal.
- **What it compares against:** the harness's own offline-validated baseline (§4.5), read from the latest run in the shared eval store at startup — same data home, no second source of truth. If no eval run exists it falls back to conservative defaults, loudly.
- **Reward-definition alignment:** live net revenue uses the same counterfactual convention as the offline metric — completions are credited against the calibration average of no-offer completion probability (`AVG_BASE_COMPLETION_PROB` from the simulator) rather than against the raw cart total, so a healthy discount-giving bandit scores positively and the comparison is like-for-like. This alignment was added after the first integration attempt exposed that naive paid-minus-base accounting would have flagged any well-trained policy as failing.
- **Trigger thresholds (concrete):** demotion fires when either signal degrades past a fixed fraction of baseline over at least 30 samples — net revenue below 50% of baseline, or compliance below 70% of baseline. With n≥30 these margins sit far beyond ordinary sampling noise; a 400-sample variance check confirms zero false positives under healthy operation.
- **Demotion:** the pipeline stops consulting the bandit entirely and routes every proposal through the existing `fallback_rule` path — the exact code path already proven for bandit abstention, so post-demotion gating and audit behavior is identical to the rule-only system by construction, not by re-testing hope. A durable record lands in the `system_events` table of the shared SQLite store explaining what happened and why.
- **Recovery is manual only:** an operator call (or `POST /watchdog/promote` with a required note) re-enables the bandit and clears the rolling windows. No auto-recovery — flapping risk isn't worth it.
- **Deterministic seeding for demos:** `RAZORPAY_AGENT_SABOTAGE_BANDIT=1` wraps the live policy in a `SabotagedPolicy` that always proposes a deliberately gate-rejected arm, driving compliance to zero on schedule. The watchdog then catches a real failure through the real pipeline on command — never by chance. `demo/run_watchdog_demo.py` walks the full arc (healthy → sabotage → auto-demotion → rule-only → manual re-promotion) in one self-contained run.

---

## 5. End-to-End Flow

1. Buyer-agent discovers the merchant's product feed (ACP) and creates a checkout session for an item.
2. The decision layer (LinUCB bandit) observes the session context (cart, allowance) and emits a `ProposedAction` — a discount or bundle suggestion, or nothing if it has low confidence.
3. The rule & policy layer evaluates the proposal as a `GateDecision`, checking it against the discount cap, bundle cap, one-offer-per-checkout limit, and the buyer-agent's spending allowance.
4. If rejected, the gate's `final_action` falls back to the plain default. If approved (possibly capped), the proposal becomes the session's real offer.
5. An `AuditEntry` is written immediately, regardless of outcome.
6. The buyer-agent completes the session using its scoped payment token; Razorpay's test-mode APIs process the charge.
7. **Graceful failure case:** if the payment token is expired or rejected at completion, the system does **not** silently retry (to avoid a duplicate charge). It rolls back any session-level offer state, moves the session to a clean failed/needs-new-authorization status, and writes an `AuditEntry` for the failure with the same rigor as a success. On the wire this maps to ACP's status enum as `not_ready_for_payment` carrying a `payment_declined` error message — the protocol has no dedicated failed status, and this mapping preserves its meaning exactly.
8. The eval harness periodically (or continuously) checks the bandit's ongoing performance against its offline-validated baseline.

## 6. Protocol Choice — Why ACP, Not UAP / AP2 / x402

The buildathon brief names NPCI's UAP and the "protocol race" (ACP, AP2, x402) as the reason this problem matters now. Each was evaluated:

- **NPCI UAP** — not implementable. As of this project's planning, UAP has no public technical specification; it is still a proposed framework awaiting RBI approval. Referenced narratively in this project's framing, not implemented.
- **AP2 (Google)** — a layer above checkout, focused on cryptographically signed authorization mandates (IntentMandate, PaymentMandate). Its core idea (a bounded, provable spending authorization) is borrowed conceptually via ACP's own delegated-payment allowance, without implementing AP2's full cryptographic mandate stack — judged as more scope than the timeline supports for a second full protocol integration.
- **x402 (Coinbase)** — HTTP-native but built around crypto/stablecoin settlement. Ruled out as a poor fit for a Razorpay INR test-mode flow.
- **ACP (OpenAI/Stripe)** — chosen. It directly addresses checkout execution (product discovery, session lifecycle, delegated payment), which is exactly what "make a merchant transactable end-to-end" requires, and its delegated-payment allowance already carries a mandate-like bounded authorization concept.

## 7. Numeric Parameters (reference)

| Parameter | Value | Rationale |
|---|---|---|
| Max discount | 12–15%, plus absolute rupee cap (implemented: **15% + ₹300**) | Above the ~3–8% passive acceptance baseline, well under the 30% high end reserved for clearance/luxury-avoidant categories; the absolute cap mirrors Razorpay's own Offers product pattern |
| Max bundle/upsell price | ~20% of cart value (implemented: **20%**) | Keeps suggestions proportionate rather than overwhelming the checkout |
| Offers per checkout | 1 | Simplest to bound and explain |
| Confidence representation | Float, 0–1 | Precise enough for the eval harness to threshold against |

## 8. Extension Points

This is what "modular, easy to add/remove" means concretely in this system:

- **Swap the decision layer:** any module that reads session context and emits a valid `ProposedAction` can replace the LinUCB bandit — a different bandit, a rules engine, anything — without touching the rule layer, audit trail, or checkout surface.
- **Add a new hard limit:** add a new check inside the rule & policy layer's gate logic. No other component needs to change.
- **Add a new checkout channel:** any new surface (e.g. a second protocol, a different buyer-agent type) only needs to produce sessions that flow through the same `ProposedAction` → `GateDecision` → `AuditEntry` pipeline.
- **Add a new action type:** extend the `ProposedAction` shape with a new `action_type` and a corresponding gate check — existing action types and their checks are unaffected.
- **Add a new currency:** money is stored as integer minor units and mapped through a `Currency` (code + `minor_unit_divisor`) in `core/currency.py`. INR/USD (÷100), JPY (÷1), KWD (÷1000) ship; the checkout session carries its `Currency`, and `to_paise`/`to_rupees` are already currency-aware. To support a new currency: add one `Currency` constant and surface it through the ACP `allowance.currency` field — no change to the gate, audit, or decision layers. The demo catalog is INR-only by design.

## 9. Explicitly Out of Scope

- No LLM anywhere in the decision-making or money-action path.
- No full NPCI UAP implementation (no public spec exists).
- No full AP2 cryptographic mandate stack (concept borrowed via ACP's delegated payment allowance instead).
- No x402 / crypto settlement.
- No multi-offer stacking per checkout.

## 10. Glossary

- **ACP (Agentic Commerce Protocol):** A public specification (OpenAI/Stripe) for how AI buyer-agents discover products and complete checkouts with merchants.
- **AP2 (Agent Payments Protocol):** Google's protocol for cryptographically proving what a human authorized an agent to spend.
- **Bandit / contextual bandit:** A machine learning approach for repeated single-step decisions (see one action, get one reward, no long-term state) — the simplest member of the reinforcement learning family.
- **LinUCB:** A specific contextual bandit algorithm that picks actions by weighing both expected reward and uncertainty, favoring under-explored options in a mathematically principled way.
- **Mandate / allowance:** A bounded, time-limited authorization (max amount + expiry) proving what a buyer-agent is permitted to spend on a human's behalf.
- **Regret (bandit metric):** How much worse a policy's choices were compared to the best possible choice in hindsight, summed over time.
- **Gate:** The rule & policy layer's act of checking a proposed action against hard limits before it's allowed to become real.

---

## 11. Implementation Stack

Chosen for legibility and solo-developer speed under the buildathon deadline. Boring on purpose — every choice below is mainstream, well-documented, and replaceable without touching the core contract.

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| HTTP surface | FastAPI | hosts the ACP checkout endpoints and the eval report endpoint in one app (§4.3, §4.5) |
| Audit storage | SQLite via stdlib `sqlite3` | durable and queryable with zero infrastructure; written through a thread pool from async handlers (§4.6) |
| Bandit math | numpy | LinUCB is small matrix algebra; no ML framework |
| Settlement | official Razorpay SDK, test mode only | §4.3 |
| Tests | pytest | each component green-tested before wiring the next |
| Charts | matplotlib | static demo plots generated from the same data the report endpoint serves (§4.5) |

Repository layout mirrors the component list one-to-one: `src/razorpay_agent/{core,gate,decision,audit,checkout,buyer,eval,watchdog}` plus `tests/` and `demo/` at the repo root. The single top-level package exists for import safety; the subpackage names match the components above. Demo tooling lives under `demo/`: `pretrain_bandit.py` (one-time warm-start generation), `run_demo.py` (quick end-to-end), `run_capture_demo.py` and `run_full_demo.py` (settlement chain and full four-phase walkthrough), `run_watchdog_demo.py` (demotion/re-promotion arc), `run_offpolicy_demo.py` (counterfactual evaluation over a freshly logged window), with artifacts written to timestamped folders under `demo/out/`.

Payment credentials come exclusively from a gitignored `.env` file at the repo root or from shell environment variables (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`) — never from code, repo files, or chat. When credentials are absent the server falls back loudly to the scripted payment provider rather than failing silently, so a demo can never be mistaken for live settlement.

No LLM dependency appears anywhere in this stack — by design (§2).
