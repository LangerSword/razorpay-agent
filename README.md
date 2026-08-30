# razorpay-agent

A merchant-side **decisioning agent** for the Razorpay AI Builders' Buildathon
(Track 01 — AI Growth & Agentic Commerce). It grows merchant revenue with
discount and bundle-upsell offers, and makes the merchant **transactable
end-to-end by an AI buyer**, while keeping every money action **explainable,
bounded, and gated** — with a visible audit trail and one failure handled
gracefully.

## The bar (from the brief)

> Every money action explainable, bounded and gated. Show the audit trail and
> one failure handled gracefully.

This system is built around that sentence:

- **Explainable** — every proposal is a `ProposedAction`; every decision a
  `GateDecision` carrying the human-readable reason and which limits were
  checked. The offline eval harness even discloses its own assumptions.
- **Bounded** — the rule & policy layer caps discount % and absolute rupee
  value, bundle share, one offer per session, and the buyer's spending
  allowance. The learned model (a LinUCB bandit) is *strictly advisory* — its
  output is never trusted directly.
- **Gated** — nothing becomes a real action without passing the gate. The gate
  always wins over the decision layer.
- **Audited** — every proposal gets exactly one `AuditEntry`, written the moment
  the gate allows it (provisional `offered`) and finalized at resolution.
- **One graceful failure** — a declined/expired payment rolls back the offer and
  returns to `not_ready_for_payment` with a `payment_declined` message, never
  retrying (no duplicate charge). The safety watchdog additionally demotes the
  bandit if it underperforms its offline baseline.

## Architecture

`architecture.md` is the single source of truth for *why* every structural
decision was made. Read it first. `prompt.md` governs how the code is changed.

## Quick start

```bash
pip install -e ".[dev]"      # httpx2, fastapi, numpy, razorpay, matplotlib
pytest -q                    # 200+ tests, includes fuzzing + eval harness

# One-command end-to-end demo (scripted payments, no Razorpay creds needed):
python demo/run_demo.py

# Full walkthrough (accept / gate-cap / graceful failure / live settlement):
python demo/run_full_demo.py --skip-payment
python demo/verify_demo.py   # assert the demo met the brief bar
```

For **live** Razorpay test-mode settlement, put `RAZORPAY_KEY_ID` /
`RAZORPAY_KEY_SECRET` in `.env` (gitignored). With no creds the server falls
back loudly to the scripted provider, so the demo is never mistaken for live.

## Layout

`src/razorpay_agent/{core,gate,decision,checkout,buyer,audit,eval,watchdog}` —
one package per component, each speaking to the immutable Core Contract. Tests
mirror the layout under `tests/`.

## Settlement note (intentional)

In test mode an order is created by the charge path; the real capture happens
when the buyer pays through the hosted Payment Link. The merchant Orders API
entry therefore remains `created` until a Standard Checkout integration binds
capture to that order id. We do **not** fabricate a capture event.
