# razorpay-agent

A merchant-side agent that proposes offers to an AI buyer over ACP and settles
them on Razorpay — every action bounded, gated, and audited. An advisory LLM
reasoner lives in `reasoning/` and is structurally barred from the money path.

> **Demo merchant:** *Plain Goods Co.* is a **fictional** merchant used only for
> this buildathon demo. It is not affiliated with, endorsed by, or representative
> of any real company. The product catalog and regimen graph are invented too.

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
- **Live reasoning (P4)** — advisory LLM reasoner live on Nous Portal (Step 3.7
  Free); tool specs include arg schemas so the model calls tools correctly in one
  shot; final answers are post-processed clean.
- **Regimen-aware (P3)** — `BundleArm.anchor_sku` + candidate-generator node in the
  MerchantAgent graph; simulator honors the co-purchase prior via
  `CoPurchaseGraph.relevant_categories`.

## Run it

```bash
pip install -e ".[dev,llm]"
pytest -q
python demo/run_full_demo.py --wait 900   # accept · gate-cap · failure · live settlement
python demo/run_reasoning_demo.py         # live Nous reasoning trace
```

Live settlement needs `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` in `.env`;
without them the server falls back loudly to the scripted provider.

## Layout

```
src/razorpay_agent/{core,gate,decision,checkout,buyer,audit,eval,watchdog,graph,reasoning,storefront}
```

A thin presentational storefront is served at `GET /storefront` (a visual layer over
the existing `/products` ACP feed, with an agent-vs-human browsing indicator).

`architecture.md` explains *why*; `prompt.md` governs *how* you change it.
