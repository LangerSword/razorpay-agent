# razorpay-agent

A merchant-side decisioning agent for the Razorpay Buildathon. It proposes
discounts and bundle upsells to an AI buyer over the Agentic Commerce Protocol,
and settles them through Razorpay. Every money move is explainable, bounded, and
gated — with an audit trail and one failure handled gracefully. No LLM anywhere.

## The bar

> Every money action explainable, bounded and gated. Show the audit trail and
> one failure handled gracefully.

- **Explainable** — each proposal is a `ProposedAction`; each decision a
  `GateDecision` carrying the reason and the limits it was checked against.
- **Bounded** — the rule layer caps discount %, rupee value, bundle share,
  offers per session, and the buyer's allowance. The bandit is advisory only.
- **Gated** — nothing acts without the gate, and the gate always wins.
- **Audited** — one `AuditEntry` per proposal, finalized as `accepted`,
  `declined`, or `failed`.
- **One failure** — a declined payment rolls back to `not_ready_for_payment`
  and is never retried. A watchdog also demotes the bandit if it drifts from
  baseline.

## How it fits

```
buyer (ACP) ─▶ /checkout_sessions ─▶ OfferPipeline
                                  │
        ProposedAction ─▶ Gate ─▶ GateDecision ─▶ AuditEntry
             ▲                    │
        LinUCB bandit        allowed? ─┼─▶ Razorpay (test-mode)
             │
        Watchdog (demotes)
```

The spine is a tiny Core Contract — `ProposedAction`, `GateDecision`,
`AuditEntry` — and one rule: nothing acts without the gate, nothing happens
unrecorded. Everything else is a swappable module.

`architecture.md` explains *why*; `prompt.md` governs *how* you change it.

## Quick start

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
pytest -q                                  # 200+ tests, fuzzing + eval harness

python demo/run_demo.py                    # scripted, no creds needed
python demo/run_full_demo.py --wait 900    # accept · gate-cap · failure · live settlement
python demo/verify_demo.py                 # assert the brief's bar was met
```

Live settlement needs `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` in `.env`
(gitignored). Without them the server falls back loudly to the scripted
provider. Run the server with `python run_server.py`.

## Demos — `demo/`

| Script | Shows |
|---|---|
| `run_demo.py` | Quick end-to-end (scripted) |
| `run_full_demo.py` | Phase A accept · B gate-cap · C graceful failure · D live Razorpay |
| `run_capture_demo.py` | Capture through a hosted Payment Link |
| `run_watchdog_demo.py` | Sabotage → demotion → rule-only → manual re-promotion |
| `run_offpolicy_demo.py` | Counterfactual estimate via inverse-propensity scoring |
| `pretrain_bandit.py` | Warm-start: 5000 episodes → `demo/pretrained_bandit.json` |
| `verify_demo.py` | Asserts a run met the brief's bar |

## Surface

ACP checkout endpoints (`/products`, `/checkout_sessions` + complete/cancel)
plus:

- `GET  /eval/report` — uplift, compliance, regret, with a honesty note
- `GET  /eval/offpolicy?alpha=0.25` — counterfactual for a different exploration setting
- `GET  /watchdog/status`, `POST /watchdog/promote` — monitor + manual re-promotion

## Proof points

- **Bounded & gated:** the bandit proposes 5% (≈₹499.90 on a ₹9,998 cart); the
  ₹300 ceiling caps it to 3.0% (₹299.94). The buyer declines the stingy offer
  yet still buys at full price.
- **Live & verified:** real Razorpay order → Payment Link → paid; Razorpay's
  own ledger (`pay_…`, 237405 paise, `captured`) matches transcript and audit
  to the paisa.
- **System safety:** forcing oversized bundles drops compliance to 60.3% — below
  the 70%-of-baseline wire — and the watchdog auto-demotes to rule-only; an
  operator re-promotes manually.
- **Rigor:** the gate is property-fuzzed at 10 invariants × 2,000 cases =
  20,000 decisions, 0 violations. The off-policy estimator reports a 95% CI,
  effective sample size, and a disclosed kernel — and says so when the result
  is a null.
- **Audited:** every decision in one SQLite store, queryable on command.

## Layout

```
src/razorpay_agent/
├── core/      contract + currency
├── gate/      hard limits, cap-down, fallback
├── decision/  LinUCB bandit + persistence
├── checkout/  ACP surface, catalog, pipeline, Razorpay
├── buyer/     scripted ACP buyer-agent
├── audit/     SQLite audit store
├── eval/      replay, report, off-policy IPS, decision log
└── watchdog/  monitor, sabotage, system events
```

## Config

| Var | Use |
|---|---|
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | test-mode creds (`.env` or env) |
| `RAZORPAY_AGENT_SABOTAGE_BANDIT=1` | make the bandit propose a gate-rejected arm (watchdog demos) |

## Settlement note

Test mode creates the order; capture happens when the buyer pays the hosted
Payment Link. The merchant Orders API entry stays `created` until a Standard
Checkout integration binds capture to that order id. We don't fabricate capture.
