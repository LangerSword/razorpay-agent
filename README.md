# razorpay-agent

A merchant-side agent that proposes offers to an AI buyer over ACP and settles
them on Razorpay — every action bounded, gated, and audited. Two Hermes-style
agents (MerchantAgent + BuyerAgent), both with their own advisory LLM reasoners,
speaking ACP end-to-end.

> **Demo merchant:** *Common* is a **fictional** merchant used only for
> this buildathon demo. It is not affiliated with, endorsed by, or representative
> of any real company. The product catalog and regimen graph are invented too.

## Safe by construction

- **Bounded** — a rule layer caps whatever the model proposes (discount %, rupee
  value, bundle size, buyer allowance).
- **Gated** — nothing reaches the buyer without passing the rule layer; it always wins.
- **Audited** — one log entry per decision, queryable end to end.
- **Self-correcting** — a watchdog demotes the model if it drifts from baseline.
- **Two agents** — MerchantAgent proposes; BuyerAgent evaluates. Both Hermes-style, both read-only tools.

## What it does

Two Hermes-style agents, both with read-only tools, speaking ACP end-to-end:

- **MerchantAgent** — a LinUCB bandit suggests an offer (discount, bundle upsell,
  or nothing). A rule layer checks it against hard limits and approves, caps, or
  rejects. An advisory LLM reasoner explains *why* through read-only tools, never
  touching the money path.
- **BuyerAgent** — its own LLM-powered reasoning evaluates the offer against
  its budget, purchase history, and the cart; it accepts or declines — not just a
  threshold policy. The purchase always happens; the buyer's verdict is about
  whether to take the offer or buy at full price.

Settlement runs through Razorpay test mode.

> Illustrative, not a feature: on a ₹9,998 cart the bandit suggests a discount
> and the ₹300 ceiling caps what the buyer sees. The offer type is just an
> example — the cap applies to whatever the model proposes. The buyer agent's
> reasoner then evaluates whether to accept or decline.

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
- **Buyer reasoning (MVP)** — BuyerAgent has its own LLM reasoner with memory
  (purchase history) and strict verdict format (`Verdict: ACCEPT` /
  `Verdict: DECLINE`); the buyer reasoner runs as a single-pass LLM call with
  clear acceptance criteria.

## Run it

```bash
pip install -e ".[dev,llm]"
pytest -q
python demo/run_full_demo.py --wait 900   # accept · gate-cap · failure · live settlement
python demo/run_reasoning_demo.py         # live Nous reasoning trace (merchant)
python demo/pretrain_reasoner.py          # regenerate few-shot examples (merchant + buyer)
```

Live settlement needs `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` in `.env`;
without them the server falls back loudly to the scripted provider.

## Frontend (React + TypeScript)

The storefront is a production-grade React app in `web/`:

```bash
cd web
npm install
npm run dev      # dev server at :5173 (proxies /api to :8613)
npm run build    # production build to dist/
npm run lint     # anti-slop Oxlint
```

FastAPI serves the production build at `GET /storefront` when `web/dist/index.html` exists.

### Stack
- **Vite + React 18 + TypeScript** — fast HMR, type-safe
- **Oxlint + anti-slop** — 15 generic lint rules for clean code
- **YC-themed design** — orange (#FF4000) + black + white editorial aesthetic
- **Zero external UI deps** — pure CSS, no component libraries

## Layout

```
src/razorpay_agent/{core,gate,decision,checkout,buyer,audit,eval,watchdog,graph,reasoning,storefront}
web/{src,dist}                                             # React frontend (Vite + TS)
```

`architecture.md` explains *why*; `prompt.md` governs *how* you change it.
