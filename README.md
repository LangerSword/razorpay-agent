# razorpay-agent

A merchant-side **decisioning agent** for the Razorpay AI Builders' Buildathon
(Track 01 — AI Growth & Agentic Commerce). It grows merchant revenue with
discount and bundle-upsell offers, and makes the merchant **transactable
end-to-end by an AI buyer**, while keeping every money action **explainable,
bounded, and gated** — with a visible audit trail and one failure handled
gracefully.

There is **no LLM anywhere** in the system. The "AI" is the eval harness, the
bandit, and the multi-agent orchestration — not a language model.

## The bar (from the brief)

> Every money action explainable, bounded and gated. Show the audit trail and
> one failure handled gracefully.

This sentence drives every structural decision:

- **Explainable** — every proposal is a `ProposedAction`; every decision a
  `GateDecision` carrying the human-readable reason and which limits were
  checked. The offline eval harness even discloses its own assumptions.
- **Bounded** — the rule & policy layer caps discount %, absolute rupee value,
  bundle share, one offer per session, and the buyer's spending allowance. The
  learned model (a LinUCB bandit) is *strictly advisory* — its output is never
  trusted directly.
- **Gated** — nothing becomes a real action without passing the gate. The gate
  always wins over the decision layer.
- **Audited** — every proposal gets exactly one `AuditEntry`, finalized at
  resolution (`accepted` / `declined` / `failed`), so each entry states final
  truth, never provisional state.
- **One graceful failure** — a declined/expired payment rolls back the offer and
  returns to `not_ready_for_payment` with a `payment_declined` message, never
  retrying (no duplicate charge). A safety watchdog additionally demotes the
  bandit if it underperforms its offline baseline.

## Architecture at a glance

```
buyer-agent (ACP)  ──▶  POST /checkout_sessions  ──▶  OfferPipeline
                                                    │
            ProposedAction ──▶ Rule & Policy Gate ──▶ GateDecision
                 ▲                           │
            LinUCB bandit              allowed? ─┼─(capped)──▶ AuditEntry
                 │                                  │
            Safety watchdog                   Razorpay (test-mode)
```

The immutable spine is the **Core Contract**: three fixed data shapes —
`ProposedAction`, `GateDecision`, `AuditEntry` — plus one rule (*nothing acts
without the gate; nothing happens unrecorded*). Every component speaks to that
contract; everything else is a swappable module.

| Component | Responsibility |
|---|---|
| `core` | The contract: `ProposedAction`, `GateDecision`, `AuditEntry`, `Currency` |
| `gate` | Deterministic hard limits (discount %, ₹ ceiling, bundle share, allowance); always wins |
| `decision` | LinUCB contextual bandit — strictly advisory, pretrained + persisted |
| `checkout` | ACP-compliant surface + Razorpay test-mode settlement |
| `buyer` | Scripted ACP-speaking buyer-agent with its own accept/decline judgment |
| `audit` | SQLite audit store (thread-pool writes) |
| `eval` | Offline replay harness + live self-reporting + off-policy counterfactual |
| `watchdog` | Rolling-window monitor → auto-demote to rule-only, manual re-promote |

`architecture.md` is the single source of truth for *why* each decision was
made. `prompt.md` governs *how* the code is changed. **Read both before
contributing.**

## Quick start

Requires **Python 3.11+**.

```bash
pip install -e ".[dev]"      # fastapi, uvicorn, numpy, razorpay, matplotlib, httpx2 + pytest, hypothesis
pytest -q                    # 200+ tests (incl. property fuzzing + eval harness)

# Quick end-to-end demo with scripted payments (no Razorpay creds needed):
python demo/run_demo.py

# Full four-phase walkthrough (accept / gate-cap / graceful failure / live settlement):
python demo/run_full_demo.py --wait 900
python demo/verify_demo.py   # assert the demo met the brief bar
```

For **live** Razorpay test-mode settlement, put `RAZORPAY_KEY_ID` /
`RAZORPAY_KEY_SECRET` in `.env` (gitignored). With no creds the server falls
back loudly to the scripted provider, so a demo is never mistaken for live.

Launch the server standalone with `python run_server.py` (or uvicorn
`razorpay_agent.server:build_live_app --factory`).

## Demos

All under `demo/`. The scripted ones need no credentials; the settlement demos
need `.env` keys.

| Script | What it shows |
|---|---|
| `run_demo.py` | Quick end-to-end on scripted payments |
| `run_full_demo.py` | **Phase A** accept · **Phase B** gate-cap · **Phase C** graceful failure · **Phase D** live Razorpay settlement |
| `run_capture_demo.py` | Razorpay capture-flow through a hosted Payment Link |
| `run_watchdog_demo.py` | Sabotage → compliance crash → auto-demotion → rule-only → manual re-promotion |
| `run_offpolicy_demo.py` | Off-policy counterfactual estimate (inverse-propensity scoring) over a logged window |
| `pretrain_bandit.py` | One-time warm-start: 5000 synthetic episodes → `demo/pretrained_bandit.json` |
| `verify_demo.py` | Asserts a demo run met the brief's bar |

## HTTP surface

Implements the **Agentic Commerce Protocol (ACP)** plus the eval/watchdog
endpoints, all in one FastAPI app:

- `GET  /products`
- `POST /checkout_sessions`, `GET/POST /checkout_sessions/{id}`
- `POST /checkout_sessions/{id}/complete`, `POST /checkout_sessions/{id}/cancel`
- `GET  /eval/report` — uplift, gate-compliance, regret (honesty note included)
- `GET  /eval/offpolicy?alpha=0.25` — counterfactual estimate for a different exploration parameter
- `GET  /watchdog/status`, `POST /watchdog/promote` — safety monitor state + manual re-promotion

## Proof points (what to show)

These are reproducible from the demos above, not claims:

- **Bounded & gated (Phase B):** the bandit proposes its preferred 5% (≈₹499.90
  on a ₹9,998 cart); the gate's absolute ₹300 ceiling binds and caps it to 3.0%
  (₹299.94). The buyer *declines* the stingy offer but still completes the
  purchase at full price — independent judgment, not a rubber stamp.
- **Live & independently verified (Phase D):** a real Razorpay order is created,
  a hosted Payment Link is generated, the buyer pays, and capture is confirmed.
  Razorpay's own ledger (`pay_…`, netbanking, 237405 paise, `captured`) matches
  the transcript and audit entry to the paisa.
- **System-level safety (watchdog):** forcing the bandit to propose oversized
  bundles collapses gate-compliance to 60.3% — below the 70%-of-baseline
  trip-wire — and the watchdog auto-demotes to the rule-only fallback; an
  operator re-promotes manually with a note.
- **Rigor:** the gate is property-fuzzed — 10 invariants × 2,000 random cases =
  **20,000 generated decisions, zero violations**. The off-policy estimator
  reports a 95% confidence interval, effective sample size, and a *disclosed*
  propensity kernel — and states honestly when the counterfactual is a null.
- **Audited & queryable:** every decision lands in one SQLite store, retrievable
  on command (accepted, capped, or `failed`).

## Project layout

```
src/razorpay_agent/
├── core/      contract: actions, gate decision, audit entry, currency
├── gate/      rule & policy layer (hard limits, cap-down, fallback)
├── decision/  LinUCB bandit (arms, context encoder, persistence)
├── checkout/  ACP surface, catalog, offer pipeline, Razorpay payments
├── buyer/     scripted ACP buyer-agent
├── audit/     SQLite audit store
├── eval/      offline replay, report, off-policy IPS, decision log
└── watchdog/  safety monitor, sabotage wrapper, system events
tests/         mirrors the layout above
demo/          runnable demos + generated artifacts (gitignored)
```

## Configuration & secrets

| Variable | Purpose |
|---|---|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Live test-mode creds (from `.env` or environment) |
| `RAZORPAY_AGENT_SABOTAGE_BANDIT=1` | Force the bandit to propose a gate-rejected arm (watchdog demos) |

With no Razorpay credentials the server falls back to the scripted provider and
says so loudly — a demo is never mistaken for live settlement.

## Settlement note (intentional)

In test mode an order is created by the charge path; the real capture happens
when the buyer pays through the hosted Payment Link. The merchant Orders API
entry therefore remains `created` until a Standard Checkout integration binds
capture to that order id. We do **not** fabricate a capture event.

## Tests

```bash
pytest -q                              # full suite
GATE_FUZZ_EXAMPLES=2000 pytest tests/test_gate_fuzzing.py -q   # stress the gate (20k cases)
```

Tests cover the contract, gate (incl. 20k-case property fuzzing), bandit +
persistence, checkout surface, buyer-agent, eval harness, off-policy estimator,
watchdog, and server wiring.
