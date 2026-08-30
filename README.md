# razorpay-agent

A merchant-side agent that proposes offers to an AI buyer over ACP and settles
them on Razorpay — every action bounded, gated, and audited. **No LLM.**

## Safe by construction

- **Bounded** — a rule layer caps whatever the model proposes (discount %, rupee
  value, bundle size, buyer allowance).
- **Gated** — nothing reaches the buyer without passing the rule layer; it always wins.
- **Audited** — one log entry per decision, queryable end to end.
- **Self-correcting** — a watchdog demotes the model if it drifts from baseline.

## What it does

The decision layer (a LinUCB bandit) suggests an offer — a discount, a bundle
upsell, or nothing. The rule layer checks it against hard limits and approves,
caps, or rejects. The buyer-agent accepts or declines on its own bar; settlement
runs through Razorpay test mode.

> Illustrative, not a feature: on a ₹9,998 cart the bandit suggests a discount
> and the ₹300 ceiling caps what the buyer sees. The offer type is just an
> example — the cap applies to whatever the model proposes.

## Proof it works

- **Live & verified** — real Razorpay order → Payment Link → paid; Razorpay's own
  ledger matches the audit to the paisa.
- **Rigor** — the gate is property-fuzzed: 20,000 generated decisions, 0 violations.
- **Honest eval** — off-policy counterfactual reports a 95% CI and a disclosed
  kernel, and says so when the result is a null.

## Run it

```bash
pip install -e ".[dev]"
pytest -q
python demo/run_full_demo.py --wait 900   # accept · gate-cap · failure · live settlement
```

Live settlement needs `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` in `.env`;
without them the server falls back loudly to the scripted provider.

## Layout

```
src/razorpay_agent/{core,gate,decision,checkout,buyer,audit,eval,watchdog}
```

`architecture.md` explains *why*; `prompt.md` governs *how* you change it.
