# razorpay-agent

> The bandit proposes a 5% discount. The rule layer caps it to ₹300.
> That single line *is* the thesis: the learned model advises — it never decides.

A merchant-side decisioning agent for the Razorpay AI Builders' Buildathon. It
proposes discounts and bundle upsells to an AI buyer over the Agentic Commerce
Protocol and settles them through Razorpay — every money move explainable,
bounded, gated, and audited. **No LLM, anywhere.**

---

## See it in one line

A real transcript from the live demo (Phase B). The pre-trained bandit
confidently proposes its preferred 5% off on a ₹9,998 cart — ₹499.90. The
gate's absolute ₹300 ceiling binds, and the buyer is shown 3% (₹299.94):

```
buyer> merchant offered 3.0% off -> decline (my bar: 5%)
buyer> but I still want the item, so I'll complete the purchase at full price
buyer> payment authorized; order order_TVmuguTWcQIsz5
gate>   warm bandit proposed its preferred 5% (= 49990 paise on this 999800 paise cart)
gate>   rupee ceiling of 30000 paise binds -> capped to 3.0% = 29994 paise, which is what the buyer was shown
```

The decision layer is advisory. The rule layer wins. And the buyer *still buys
at full price* — independent judgment, not a rubber stamp.

## The brief's bar

| | |
|---|---|
| **Explainable** | each proposal is a `ProposedAction`; each decision a `GateDecision` with its reason and the limits it was checked against |
| **Bounded** | the rule layer caps discount %, rupee value, bundle share, offers per session, and the buyer's allowance; the bandit is advisory only |
| **Gated** | nothing acts without the gate, and the gate always wins |
| **Audited** | one `AuditEntry` per proposal, finalized `accepted` / `declined` / `failed` |
| **One failure** | a declined payment rolls back and is never retried; a watchdog demotes the bandit if it drifts from baseline |

## Live, and independently verified

A real Razorpay order is created, a hosted Payment Link is generated, the buyer
pays, and capture is confirmed. Then we check Razorpay's own ledger — not our
word:

```
LEDGER: pay_TVmw8KiQ0eNX2T method=netbanking amount=237405 status=captured
```

**237,405 paise** — exactly the 2,374.05 from the transcript and the audit
entry. Three independent records, one number, to the paisa.

## Safety that triggers itself

Flip a sabotage switch and the bandit always proposes an oversized bundle. The
gate rejects every one; compliance collapses to **60.3%** — below the 70%-of-
baseline trip-wire — and the watchdog **auto-demotes** the bandit to the safe
rule-only fallback. Recovery is manual only: an operator re-promotes with a note.
No flapping, no silent trust restored.

## Rigor, stated honestly

- The gate is property-fuzzed: **10 invariants × 2,000 random cases = 20,000
  decisions, 0 violations.**
- The off-policy estimator asks *"what would a less-exploring policy have
  earned?"* and answers with a 95% confidence interval, an effective sample size,
  and a *disclosed* propensity kernel — and says so when the result is a null.

## Audited, and queryable

Every decision — accepted, capped, or failed — lands in one SQLite store and is
queryable on command. The `failed` row is the one graceful failure: payment
declined, never retried, cleanly rolled back.

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

A tiny Core Contract — `ProposedAction`, `GateDecision`, `AuditEntry` — plus one
rule: *nothing acts without the gate, nothing happens unrecorded.* Everything
else is a swappable module. `architecture.md` explains *why*; `prompt.md`
governs *how* you change it.

## Quick start

Requires Python 3.11+.

```bash
pip install -e ".[dev]"
pytest -q                                 # 200+ tests

python demo/run_demo.py                   # scripted, no creds needed
python demo/run_full_demo.py --wait 900   # accept · gate-cap · failure · live settlement
python demo/verify_demo.py                # assert the brief's bar was met
```

Live settlement needs `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` in `.env`
(gitignored); without them the server falls back loudly to the scripted
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

ACP checkout endpoints (`/products`, `/checkout_sessions` + complete/cancel) plus:

- `GET  /eval/report` — uplift, compliance, regret, with a honesty note
- `GET  /eval/offpolicy?alpha=0.25` — counterfactual for a different exploration setting
- `GET  /watchdog/status`, `POST /watchdog/promote` — monitor + manual re-promotion

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
