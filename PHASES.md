# PHASES.md — razorpay-agent build phases

**Purpose:** single cross-session context file. Read this first at the start of every
session to regain lost context without re-reading long transcripts.

**Gate rule (from the build brief):** P2 and P3 are gated behind "a rough
pitch/video" existing. That gate is satisfied by `PITCH.md`, so both P2 and P3 were
authorized.

---

## P0 — Foundation + warm-start bandit
- Core contract: `ProposedAction` / `GateDecision` / `AuditEntry` (see `architecture.md` §3).
- Rule & policy gate: 15% + ₹300 discount cap, 20% bundle/upsell cap, one offer per
  checkout, buyer-agent allowance check. Rule layer always wins.
- Decision layer: LinUCB contextual bandit, strictly advisory.
- One-time warm-start snapshot `demo/pretrained_bandit.json` (5,000 synthetic pretrain
  episodes through the real gate); `run_server.py` warm-starts from it.
- Repo: `core/`, `gate/`, `decision/linucb.py`, `audit/`, `demo/pretrain_bandit.py`.

## P1 — Transactability + rigor proof
- ACP checkout surface (FastAPI) implementing the Agentic Commerce Protocol.
- Scripted ACP-speaking buyer-agent (discover → session → offer review → complete).
- Live Razorpay test-mode settlement (order creation, payment link, capture) — Phases B/D.
- Safety watchdog: sabotage → auto-demotion → manual re-promotion.
- Eval rigor: gate fuzzing (10 invariants × 2,000 cases) + off-policy IPS counterfactual.
- Repo: `checkout/`, `buyer/`, `watchdog/`, `eval/`, `tests/test_gate_fuzzing.py`,
  `tests/test_offpolicy.py`.

## P2 — MerchantAgent / reasoning (authorized once `PITCH.md` existed)
- Graph-based merchant-agent orchestration + reasoning node + storefront.
- Repo: `merchant.py`, `graph/merchant_graph.py`, `reasoning/`, `storefront/`.

## P3 — Regimen graph (current)
- `decision/co_purchase_graph.py`: MerchantAgent graph state holding the merchant's
  regimen / co-purchase relationships as a **documented prior** (edge weight = regimen
  strength, degree = popularity proxy).
- `candidate_bundles_for(...)`: the **candidate-generator node** — given a target SKU,
  returns regimen-anchored `BundleArm`s (each with `anchor_sku == target_sku`).
- `BundleArm.anchor_sku` added (regimen-anchored bundles; static catalog bundles leave
  it `None`). Serialization in `decision/linucb.py` is backward-compatible (old snapshots
  load with `anchor_sku=None`).
- `eval/replay.py` simulator **honors the prior**: bundle relevance is now derived from
  `CoPurchaseGraph.relevant_categories(...)` instead of naive category equality. Reward
  formula shape unchanged — it reuses `BUNDLE_RELEVANT/IRRELEVANT_TAKE_RATE`.
- Tests: `tests/test_co_purchase.py`.

## P4 — Live LLM reasoner providers (current)
- Enables **live** LLM providers inside the *isolated, advisory* `reasoning/` module only
  (it explains *why* an offer was proposed; it never proposes or executes settlement — the
  money path / bandit / gate are untouched). This does **not** put the LLM on the
  decision/money path; the hard constraints keep it off that path, so it is the one
  permitted boundary.
- `decision/co_purchase_graph.py` is unrelated; P4 is about `reasoning/llm.py`.
- Added OpenAI-compatible backends `TencentBackend` (`TENCENT_HY3_API_KEY`/`_BASE_URL`) and
  `NousBackend` (`NOUS_PORTAL_API_KEY`/`_BASE_URL`), selectable via
  `RAZORPAY_AGENT_LLM_PROVIDER=tencent|nous`. Any failure (missing key/SDK) safely falls back
  to the keyless `StubBackend`, so the demo always runs.
- `.env` loader (`load_dotenv_into_env`) exports only LLM keys into `os.environ` (Razorpay
  creds are intentionally left to the server's own loader) — scoped, no secret leakage.
- `openai` added as an **optional** dependency (`[llm]` extra) so the core install stays LLM-free.
- Tests: `tests/test_reasoning_llm.py`.

---

> **Reconstruction note:** the P0–P2 boundary text above was reconstructed from the
> code tree + conversation history (the phase brief was never a repo file). Correct any
> mislabeling if found. P3 is described from the implemented code.
