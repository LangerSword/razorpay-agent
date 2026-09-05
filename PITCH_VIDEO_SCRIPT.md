# RAZORPAY-AGENT PITCH — FINAL SCRIPT

---

## SHOT 1 — OPENING (0:00–0:25)

**ON SCREEN:**
- Title card: "razorpay-agent"
- Browser: `http://localhost:8613/products` (General Goods Co. storefront)

**SAY (voiceover):**
> "razorpay-agent is a merchant-side decisioning agent that grows revenue by proposing discounts and bundle upsells — hosted behind a real agent-to-agent commerce protocol, the Agentic Commerce Protocol, so an actual AI buyer-agent can discover the merchant, transact, and receive those offers end-to-end. Every money action is explainable, bounded, and gated, with a visible audit trail — and one failure handled gracefully."

---

## SHOT 2 — GATE-CAP MOMENT (0:25–1:35)

**ON SCREEN (paste this transcript block):**
```
  buyer> merchant offered 3.0% off -> decline (my bar: 5%)
  buyer> but I still want the item, so I'll complete the purchase at full price
  buyer> [counter] would take it at 6% off — bandit capped, so this round completes at full price
  buyer> payment authorized; order order_TVmuguTWcQIsz5
  gate>   warm bandit proposed its preferred 5% (= 49990 paise on this 999800 paise cart)
  gate>   rupee ceiling of 30000 paise binds -> capped to 3.0% = 29994 paise, which is what the buyer was shown
  gate>   (completed-session payloads clear applied offers by design; the buyer transcript above is the rendered-offer record)
```

**SAY:**
> "Here is 'bounded and gated' in one line. The bandit — pre-trained on five thousand episodes — confidently proposes its preferred 5% off. That's ₹499.90 on this cart. But the rule layer has an absolute rupee ceiling of ₹300. So the gate caps the bandit's own preferred arm down to 3% — ₹299.94 — and that's what the buyer actually sees. The decision layer is advisory; the rule layer wins. Note the buyer's own line: it *declines* the offer as not generous enough — then fires a **counter-offer** at 6%, which the cap blocks. It's not a rubber stamp; it's a negotiating agent with its own bar."

**ON SCREEN (paste buyer reasoning trace):**
```
  [buyer_reasoner] tool: check_budget(cart_total=999800, offer_discount=29994)
  [buyer_reasoner] tool: evaluate_offer(percent=3.0, my_min=5.0, category='skincare')
  [buyer_reasoner] tool: get_purchase_history(category='skincare', months=6)
  [buyer_reasoner] → "3% is below my 5% threshold for non-regimen items; my history
                       shows I already stock this SKU. Countering at 6% — if capped,
                       I'll still complete at full price."
```

**SAY:**
> "And the buyer isn't a rubber stamp or a dumb threshold. It's a real agent with memory: it checks its budget, evaluates the offer against its own bar, recalls its purchase history, and **counters**. That reasoning trace is written to its own side table — `buyer_reasoning_log`, separate from the merchant's `reasoning_log` — so both sides of the negotiation are independently auditable."

**ON SCREEN (paste audit entry):**
```json
"proposed_action": {... "discount_percent": 5.0 ...}
"gate_decision": {"allowed": true, "reason": "rupee discount capped at 300.00 (3% of cart)",
                  "final_action": {... "discount_percent": 3.0 ...}}
"outcome": {"status": "accepted", "detail": "buyer completed; paid 9698.06 vs base 9998.00"}
```

**SAY:**
> "And it's not just on screen — the audit entry records the proposed 5%, the capped 3%, and the exact reason. Explainable, by construction."

---

## SHOT 3 — LIVE SETTLEMENT + LEDGER (1:35–2:50)

**ON SCREEN (paste Phase D tail):**
```
  [00:45:12] merchant order order_TVmuhNG3bCjuJ5 status per Razorpay: created (amount 237405 paise, paid so far 0)
  [00:45:13] payment link plink_TVmuj8E7Tc1ROA created
>>> OPEN AND PAY (netbanking Success button): https://rzp.io/rzp/6Fc2Mfg
  [00:46:40] link status per Razorpay: paid
>>> CAPTURE CONFIRMED by Razorpay. Merchant order order_TVmuhNG3bCjuJ5 remains 'created' in the Orders API; capture settled through the payment link's own Razorpay order.
```

**SAY:**
> "Now the live path. A real Razorpay order is created — ₹2,374.05 — a hosted payment link is generated, and the buyer pays through Razorpay's own test-mode checkout. The moment Razorpay reports 'paid', we report capture confirmed. We never claim success the API hasn't confirmed."

**ON SCREEN (paste ledger output — run live or screenshot from existing):**
```
LEDGER: pay_TVmw8KiQ0eNX2T method=netbanking amount=237405 status=captured
```

**SAY:**
> "Then we go to the source of truth — Razorpay's own ledger, not our word. The captured payment is `pay_TVmw8KiQ0eNX2T`, netbanking, **237,405 paise** — exactly the 2,374.05 from the transcript and from the audit entry. Three independent records, one number, to the paisa."

---

## SHOT 4 — WATCHDOG (2:50–3:50)

**ON SCREEN (paste from demo/out/watchdog_run.txt):**
```
=== PHASE 1: 35 healthy sessions ===
  watchdog status: demoted=False; rolling=mean_net_revenue=303.9 over 35, compliance=100% over 35
=== PHASE 2: SABOTAGE ON for 34 sessions (always proposes oversized bundle) ===
  [watchdog] DEMOTING decision layer to rule-only fallback: rolling gate compliance 60.3% fell below 70% of offline baseline 87.2% over 58 proposals
  session 58: proposed [fallback_rule] -> declined, bought anyway
  watchdog status: demoted=True
  reason: rolling gate compliance 60.3% fell below 70% of offline baseline 87.2% over 58 proposals
=== PHASE 3: 3 sessions under RULE-ONLY fallback ===
  every proposal above sources 'fallback_rule'; bandit not consulted.
=== PHASE 4: operator re-promotion (manual switch) ===
  [watchdog] RE-PROMOTING decision layer to bandit (operator note: offline revalidation passed; promoting bandit)
```

**SAY:**
> "Bounded and gated at the transaction level isn't enough — the *learned model* itself needs a leash. We flip a sabotage switch so the bandit always proposes an oversized bundle. The gate rejects every one — watch the compliance collapse to 60.3%, below the 70% of baseline trip-wire — and the watchdog **auto-demotes** the bandit to the safe rule-only fallback. Recovery is manual only: an operator re-promotes with a note. No flapping, no silent trust restored."

**OPTIONAL — paste durable system-events record:**
```
  [00:55:37] demotion: {"reason": "rolling gate compliance 60.3% fell below 70% of offline baseline 87.2%..."}
```

---

## SHOT 5 — RIGOR: FUZZ + OFF-POLICY (3:50–4:25)

**ON SCREEN (paste fuzz output):**
```
$ GATE_FUZZ_EXAMPLES=2000 pytest tests/test_gate_fuzzing.py -q
10 passed in 13.18s
```

**SAY:**
> "The gate isn't eyeballed — it's property-fuzzed. Ten invariants, two thousand random cases each: **20,000 generated decisions, zero violations**."

**ON SCREEN (paste off-policy block from demo/out/offpolicy_run.txt):**
```
  estimated net revenue per decision: 292.85 rupees (95% CI 272.23 .. 313.47)
  effective sample size: 260 of 260 logged decisions
  snapshot agreement rate: 100%
  reward metric: aligned net revenue (§4.7 counterfactual convention)
  method: Self-normalized inverse propensity scoring under a stated uniform-exploration kernel (epsilon=0.05); both policies scored statically from the pretrained snapshot; candidate differs only in alpha.
```

**SAY:**
> "And when we ask a counterfactual — what would a less-exploring policy have earned? — we report it with a 95% confidence interval, an effective sample size, and a disclosed propensity kernel. The honest result: on this window the answer is 'nothing would change' — and we say so, rather than dress up a null as a win."

---

## SHOT 6 — AUDIT TRAIL (4:25–4:45)

**ON SCREEN (paste audit query output):**
```
checkout_session_5a62208e98fb4d95 | accepted | ('max_discount_pct', 'max_discount_rupee_cap', 'buyer_allowance')
checkout_session_d9dec67a2d214865 | failed | ('max_discount_pct', 'max_discount_rupee_cap', 'buyer_allowance')
checkout_session_9e881605e017476e | accepted | ('max_discount_pct', 'max_discount_rupee_cap', 'buyer_allowance')
```

**SAY:**
> "Every decision — accepted, capped, or failed — lands in one SQLite store and is queryable on command. The `failed` row there is our one graceful failure, from Phase C: payment declined, never retried, cleanly rolled back."

**ON SCREEN (paste Phase C line):**
```
buyer> completion failed: Payment Declined By Provider; not retrying
```

---

## SHOT 7 — CLOSING (4:45–5:00)

**ON SCREEN:**
Five-word card (big, centered):
# explainable · bounded · gated · audited · one failure handled gracefully

**SAY:**
> "Explainable — every entry carries its reason. Bounded — a hard rupee ceiling cuts the model down to size. Gated — the rule layer rejects what it shouldn't, and demotes the model when it drifts. Audited — one queryable store, start to finish. And one failure, handled gracefully: declined, never retried, fully logged. That's razorpay-agent."

---

## END

(Black screen, fade out.)
