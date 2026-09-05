# RAZORPAY-AGENT — 5-MINUTE PITCH (Single Narration)

---

*[Camera on you. Storefront visible in background or picture-in-picture.]*

---

razorpay-agent is a merchant-side decisioning agent that grows revenue by proposing discounts and bundle upsells — hosted behind a real agent-to-agent commerce protocol, the Agentic Commerce Protocol, so an actual AI buyer-agent can discover the merchant, transact, and receive those offers end-to-end. Every money action is explainable, bounded, and gated, with a visible audit trail — and one failure handled gracefully.

Let me show you what that means in practice.

This is General Goods Co. — a fictional merchant we built to demo the system. An AI buyer walks in. It's not a script. It's not a threshold. It's a real agent with memory, a budget, and a bar for what discount it will accept.

Here's the moment that defines the whole system. The bandit — pre-trained on five thousand episodes — confidently proposes its preferred five percent off. That's ₹499.90 on this cart. But the rule layer has an absolute rupee ceiling of ₹300. So the gate caps the bandit's own preferred arm down to three percent — ₹299.94 — and that's what the buyer actually sees. The decision layer is advisory. The rule layer wins.

Now watch the buyer's response. It declines the offer — says "my bar is five percent" — then fires a counter-offer at six percent. The cap blocks that too. It's not a rubber stamp. It's a negotiating agent with its own bar. And it knows what it already owns. It checks its budget. It evaluates the offer. It recalls its purchase history. That reasoning trace is written to its own side table — separate from the merchant's — so both sides of the negotiation are independently auditable.

And it's not just on screen. The audit entry records the proposed five percent, the capped three percent, and the exact reason. Three independent records — the transcript, the audit entry, and the reasoning trace — all telling the same story.

Now the live path. A real Razorpay order is created — ₹2,374.05 — a hosted payment link is generated, and the buyer pays through Razorpay's own test-mode checkout. The moment Razorpay reports paid, we report capture confirmed. We never claim success the API hasn't confirmed.

Then we go to the source of truth — Razorpay's own ledger, not our word. The captured payment, netbanking, 237,405 paise — exactly the ₹2,374.05 from the transcript and from the audit entry. Three independent records, one number, to the paisa.

Bounded and gated at the transaction level isn't enough — the learned model itself needs a leash. We flip a sabotage switch so the bandit always proposes an oversized bundle. The gate rejects every one. The compliance collapses to sixty-point-three percent, below the seventy-percent-of-baseline trip-wire, and the watchdog auto-demotes the bandit to the safe rule-only fallback. Recovery is manual only. An operator re-promotes with a note. No flapping. No silent trust restored.

The gate isn't eyeballed — it's property-fuzzed. Ten invariants, two thousand random cases each: twenty thousand generated decisions, zero violations.

And when we ask a counterfactual — what would a less-exploring policy have earned — we report it with a ninety-five percent confidence interval, an effective sample size, and a disclosed propensity kernel. The honest result: on this window the answer is "nothing would change" — and we say so, rather than dress up a null as a win.

Every decision — accepted, capped, or failed — lands in one SQLite store and is queryable on command. The failed row is our one graceful failure: payment declined, never retried, fully logged. The system doesn't flinch.

So: explainable — every entry carries its reason. Bounded — a hard rupee ceiling cuts the model down to size. Gated — the rule layer rejects what it shouldn't, and demotes the model when it drifts. Audited — one queryable store, start to finish. And one failure, handled gracefully: declined, never retried, fully logged.

That's razorpay-agent.

---

*[End on five-word card: explainable · bounded · gated · audited · one failure handled gracefully]*
