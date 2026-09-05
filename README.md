<div align="center">

# razorpay-agent

**A merchant-side AI agent that proposes offers to an autonomous buyer over ACP and settles them on Razorpay — every action bounded, gated, and audited.**

[![Vercel](https://img.shields.io/badge/Vercel-Deployed-000?style=flat-square&logo=vercel)](https://razorpay-agent.vercel.app)
[![Python](https://img.shields.io/badge/Python-3.14-3776AB?style=flat-square&logo=python)](https://python.org)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?style=flat-square&logo=typescript)](https://typescriptlang.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

[Demo](https://razorpay-agent.vercel.app) · [Architecture](architecture.md) · [Docs](docs/)

![Common Storefront](docs/screenshot.png)

</div>

---

## What is this?

razorpay-agent is a **dual-agent commerce system** where a merchant-side AI proposes offers to an autonomous buyer agent over the Agentic Commerce Protocol (ACP), and settles transactions on Razorpay. Every money action is **bounded, gated, and audited** — the LLM advises, never executes.

Built for the **Razorpay AI Builders' Buildathon 2026** (Track 01: AI Growth & Agentic Commerce).

---

## Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- Razorpay test-mode keys (optional — falls back to scripted provider)

### Backend

```bash
pip install -e ".[dev,llm]"
python run_server.py
# → http://localhost:8613
```

### Frontend

```bash
cd web
npm install
npm run dev
# → http://localhost:5173
```

### Full Demo

```bash
python demo/run_full_demo.py --wait 900
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENT COMMERCE                           │
│                                                                 │
│  ┌──────────────┐    ACP     ┌──────────────┐                  │
│  │ BuyerAgent   │ ◄────────► │ MerchantAgent│                  │
│  │ (LLM reasoner│            │ (LinUCB + LLM│                  │
│  │  + memory)   │            │  reasoner)   │                  │
│  └──────┬───────┘            └──────┬───────┘                  │
│         │                           │                          │
│         └───────────┬───────────────┘                          │
│                     ▼                                           │
│            ┌────────────────┐                                   │
│            │  GATE (rules)  │ ← Always wins over LLM            │
│            └───────┬────────┘                                   │
│                    ▼                                            │
│            ┌────────────────┐                                   │
│            │   RAZORPAY     │ ← Settlement                      │
│            └───────┬────────┘                                   │
│                    ▼                                            │
│            ┌────────────────┐                                   │
│            │  AUDIT TRAIL   │ ← Every action logged             │
│            └────────────────┘                                   │
└─────────────────────────────────────────────────────────────────┘
```

### Two Agents, One Gated Money Path

| Component | Role | LLM? |
|-----------|------|------|
| **MerchantAgent** | Proposes offers via LinUCB bandit + advisory reasoner | Advisory only |
| **BuyerAgent** | Evaluates offers, accepts/declines with memory | Yes (read-only tools) |
| **Gate** | Caps/rejects proposals against hard limits | No (deterministic) |
| **Settlement** | Razorpay order → payment link → capture | No |
| **Audit** | Logs every proposal, decision, outcome | No |

---

## Tech Stack

### Backend
- **FastAPI** — async API framework
- **LangGraph** — agent orchestration
- **LinUCB** — contextual bandit for offer selection
- **Razorpay SDK** — payment settlement
- **Pydantic** — data validation

### Frontend
- **React 18** + **TypeScript** — UI framework
- **Vite** — build tool
- **Oxlint** + **anti-slop** — linting
- **Pure CSS** — zero UI libraries

### Infrastructure
- **Vercel** — hosting (serverless Python + static React)
- **GitHub Actions** — CI/CD (optional)

---

## Features

### Safe by Construction
- **Bounded** — rule layer caps whatever the model proposes
- **Gated** — nothing reaches the buyer without passing the gate
- **Audited** — one log entry per decision, queryable end-to-end
- **Self-correcting** — watchdog demotes the model if it drifts

### Live Agent Reasoning
- Advisory LLM reasoner explains *why* through read-only tools
- Tool specs include arg schemas for correct one-shot calls
- Degrades to keyless stub if LLM unavailable

### Autonomous Buyer
- LLM-powered reasoning with purchase history memory
- Strict verdict format (`ACCEPT` / `DECLINE`)
- Single-pass evaluation with clear acceptance criteria

### Production Frontend
- YC-themed editorial design (orange + black + white)
- Animated splash screen
- Real-time agent status panel
- Product grid with filters, cart, modal
- Zero emoji usage — premium minimal aesthetic

---

## Deployment

### Vercel (Recommended)

1. Fork this repo
2. Import to [Vercel](https://vercel.com/new)
3. Set environment variables:
   ```
   RAZORPAY_KEY_ID=rzp_test_xxx
   RAZORPAY_KEY_SECRET=xxx
   RAZORPAY_AGENT_LLM_PROVIDER=stub
   ```
4. Deploy — Vercel auto-detects `vercel.json`

### Manual

```bash
# Build frontend
cd web && npm install && npm run build && cd ..

# Run backend
pip install -e ".[dev,llm]"
python run_server.py
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RAZORPAY_KEY_ID` | No | — | Razorpay test/live key |
| `RAZORPAY_KEY_SECRET` | No | — | Razorpay secret |
| `RAZORPAY_AGENT_LLM_PROVIDER` | No | `stub` | `stub`, `openai`, `anthropic`, `nous` |

---

## Project Structure

```
razorpay-agent/
├── api/
│   └── index.py              # Vercel serverless entry
├── web/                      # React frontend (Vite + TS)
│   ├── src/
│   │   ├── components/       # UI components
│   │   ├── context/          # State management
│   │   ├── types/            # TypeScript types
│   │   └── index.css         # Design system
│   ├── tools/oxlint/         # anti-slop lint plugin
│   └── dist/                 # Production build
├── src/razorpay_agent/
│   ├── core/                 # Core contract (frozen)
│   ├── gate/                 # Rule & policy layer
│   ├── decision/             # LinUCB bandit + regimen graph
│   ├── checkout/             # ACP API + Razorpay settlement
│   ├── buyer/                # BuyerAgent
│   ├── reasoning/            # Advisory LLM reasoners
│   ├── shop/                 # Shop assistant
│   ├── merchant.py           # MerchantAgent graph
│   ├── server.py             # FastAPI app factory
│   └── eval/                 # Eval harness + watchdog
├── demo/                     # Demo scripts + pretrain
├── tests/                    # Pytest suite
├── vercel.json               # Vercel deployment config
├── architecture.md           # Why every decision was made
├── prompt.md                 # How to change the system
└── README.md                 # This file
```

---

## Proof It Works

- **Live & verified** — real Razorpay order → Payment Link → paid
- **Rigor** — gate property-fuzzed: 20,000 decisions, 0 violations
- **Honest eval** — off-policy counterfactual with 95% CI
- **Live reasoning** — advisory LLM reasoner on Nous Portal (free tier)

---

## License

MIT © 2026 Lakshaya

---

<div align="center">

**[Demo](https://razorpay-agent.vercel.app)** · [Architecture](architecture.md) · [Docs](docs/)

Built for the Razorpay AI Builders' Buildathon 2026

</div>
