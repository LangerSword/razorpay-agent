from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from razorpay_agent.reasoning.agent import ReasoningAgent

from razorpay_agent.audit import AuditStore
from razorpay_agent.checkout.sessions import (
    AppliedOffer,
    CheckoutSessionState,
    to_rupees,
)
from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.core.audit import (
    ACCEPTED,
    DECLINED,
    FAILED,
    OFFERED,
    AuditEntry,
    AuditOutcome,
)
from razorpay_agent.core.decisions import GateDecision
from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.eval.synthetic import (
    AVG_BASE_COMPLETION_PROB,
    carrying_cost_penalty_rupees,
    clearance_relief_rupees,
    is_deep_discount_arm,
)
from razorpay_agent.gate.context import SessionContext
from razorpay_agent.gate.gate import RulePolicyGate, RulePolicyGateConfig

FALLBACK_SOURCE = "fallback_rule"

EXPECTED_NO_OFFER_FRACTION = AVG_BASE_COMPLETION_PROB


# For stale-stock clearance, only meaningful (deep) discounts are worth
# offering (see CLEARANCE_MIN_DISCOUNT_PCT in razorpay_agent.eval.synthetic).
class OfferPipeline:
    def __init__(
        self,
        policy: LinUCBPolicy | None,
        gate_config: RulePolicyGateConfig,
        audit_store: AuditStore,
        watchdog=None,
        decision_log=None,
        temperature: float = 0.0,
        rng=None,
        reasoning_agent: "ReasoningAgent | None" = None,
    ) -> None:
        self._policy = policy
        self._gate = RulePolicyGate(gate_config)
        self._gate_config = gate_config
        self._audit = audit_store
        self._watchdog = watchdog
        self._decision_log = decision_log
        self._temperature = temperature
        self._rng = rng
        self._graph = None
        self._reasoning = reasoning_agent
        self._force_next_arm: str | None = None

    def force_next_arm(self, arm_id: str | None) -> None:
        """Force the next proposal to use a specific bandit arm (demo/debug).

        The forced arm still passes through the gate, so this deterministically
        demonstrates gate-capping behavior. Pass ``None`` to clear."""
        self._force_next_arm = arm_id

    def attach_graph(self) -> None:
        """Wrap the decision flow in a LangGraph StateGraph (see graph/merchant_graph).

        Once attached, ``propose_for_session`` routes through the graph. The graph
        nodes call the same underlying ``_step_*`` methods, so the two paths are
        behaviourally identical (see tests/test_merchant_graph.py)."""
        from razorpay_agent.graph.merchant_graph import MerchantAgentGraph

        self._graph = MerchantAgentGraph(self)

    def propose_for_session(
        self,
        state: CheckoutSessionState,
        cart_value_paise: int,
        target_sku: str,
        category: str,
    ) -> AppliedOffer | None:
        if state.applied_offer is not None:
            return state.applied_offer

        if self._graph is not None:
            offer = self._graph.propose_for_session(state, cart_value_paise, target_sku, category)
        else:
            decision_context = self._step_context(state, cart_value_paise, target_sku, category)
            arm_id, action, bandit_consulted = self._step_consult(
                state, decision_context, cart_value_paise
            )
            decision = self._step_gate(state, action, cart_value_paise)
            offer = self._step_finalize(
                state, action, decision_context, decision, arm_id, bandit_consulted,
                cart_value_paise, target_sku, category,
            )

        # Schedule LLM reasoning as a background thread (non-blocking).
        # The buyer gets an immediate response; the reasoner runs async and
        # updates state.reasoning_trace when done.
        if offer is not None and offer.gate_decision.allowed:
            self.schedule_reasoning(
                state, offer.proposed_action, offer.decision_context,
                offer.gate_decision, offer.arm_id,
            )
        return offer

    def _step_context(
        self,
        state: CheckoutSessionState,
        cart_value_paise: int,
        target_sku: str,
        category: str,
    ) -> DecisionContext:
        return DecisionContext(
            session_id=state.id,
            target_sku=target_sku,
            item_category=category,
            cart_value_inr=to_rupees(cart_value_paise, state.currency),
            buyer_allowance_inr=to_rupees(state.allowance_max_paise, state.currency),
            is_stagnant=state.is_stagnant,
            days_in_stock=state.days_in_stock,
        )

    def _step_consult(
        self,
        state: CheckoutSessionState,
        decision_context: DecisionContext,
        cart_value_paise: int,
    ) -> tuple[str | None, ProposedAction | None, bool]:
        bandit_consulted = self._policy is not None and not (
            self._watchdog is not None and self._watchdog.demoted
        )
        arm_id: str | None = None
        action: ProposedAction | None = None
        if bandit_consulted:
            if self._force_next_arm is not None:
                # Demo/debug: deterministically force a specific arm (still gated).
                forced_id = self._force_next_arm
                if forced_id not in self._policy._arms:
                    forced_id = None
                if forced_id is not None:
                    arm = self._policy._arms[forced_id]
                    ctx = decision_context
                    features = self._policy._encoder.encode(ctx)
                    expected, bonus = self._policy._score(forced_id, features)
                    confidence = abs(expected) / (abs(expected) + bonus) if (abs(expected) + bonus) > 0 else 0.0
                    action = self._policy._to_action(arm, ctx, expected, confidence)
                    arm_id = forced_id
                self._force_next_arm = None
            else:
                # Both a deep discount and a bundle upsell are valid ways to clear
                # stale stock. Token discounts (5-15%) do not move dead inventory, so
                # for stagnant sessions the discount arms are restricted to the deeper
                # ones while the bundle remains available. Selection uses softmax
                # sampling (temperature > 0) when configured, else greedy argmax.
                allowed = None
                if state.is_stagnant:
                    allowed = [
                        aid
                        for aid in self._policy.arm_ids
                        if aid.startswith("b") or is_deep_discount_arm(aid)
                    ]
                arm_id, action = self._policy.propose_with_arm(
                    decision_context,
                    allowed_arm_ids=allowed,
                    temperature=self._temperature,
                    rng=self._rng,
                )
        if action is None:
            action = self._fallback_action(state.id, to_rupees(cart_value_paise, state.currency))
            arm_id = None
        return arm_id, action, bandit_consulted

    def _step_gate(
        self,
        state: CheckoutSessionState,
        action: ProposedAction,
        cart_value_paise: int,
    ) -> "GateDecision":
        gate_context = SessionContext(
            session_id=state.id,
            cart_value_inr=to_rupees(cart_value_paise, state.currency),
            buyer_allowance_inr=to_rupees(state.allowance_max_paise, state.currency),
            already_offered=False,
            is_stagnant=state.is_stagnant,
        )
        return self._gate.evaluate(action, gate_context)

    def _step_finalize(
        self,
        state: CheckoutSessionState,
        action: ProposedAction,
        decision_context: DecisionContext,
        decision: "GateDecision",
        arm_id: str | None,
        bandit_consulted: bool,
        cart_value_paise: int,
        target_sku: str,
        category: str,
    ) -> AppliedOffer:
        audit_entry_id = None
        if decision.allowed:
            audit_entry_id = self._audit.append(
                AuditEntry(
                    timestamp=datetime.now(UTC),
                    session_id=state.id,
                    proposed_action=action,
                    gate_decision=decision,
                    outcome=AuditOutcome(OFFERED, "offer presented; awaiting buyer resolution"),
                )
            )

        offer = AppliedOffer(
            proposed_action=action,
            gate_decision=decision,
            arm_id=arm_id,
            decision_context=decision_context,
            bandit_proposed=bandit_consulted,
            discount_paise=self._discount_paise(decision.final_action, cart_value_paise),
            bundle_price_paise=self._bundle_price_paise(decision.final_action),
            audit_entry_id=audit_entry_id,
        )
        state.applied_offer = offer

        if self._watchdog is not None and bandit_consulted:
            if arm_id is None:
                self._watchdog.observe_gate_outcome(None)
                self._watchdog.observe_reward(0.0)
            else:
                unmodified = decision.allowed and _actions_equivalent(
                    action, decision.final_action
                )
                self._watchdog.observe_gate_outcome(unmodified)

        if not decision.allowed:
            self._audit.append(
                AuditEntry(
                    timestamp=datetime.now(UTC),
                    session_id=state.id,
                    proposed_action=action,
                    gate_decision=decision,
                    outcome=AuditOutcome(DECLINED, f"gate rejected: {decision.reason}"),
                )
            )

        if self._decision_log is not None and bandit_consulted:
            unmodified_flag = None if arm_id is None else (
                decision.allowed and _actions_equivalent(action, decision.final_action)
            )
            self._decision_log.log_decision(
                session_id=state.id,
                item_category=category,
                cart_value_rupees=to_rupees(cart_value_paise),
                buyer_allowance_rupees=to_rupees(state.allowance_max_paise),
                target_sku=target_sku,
                arm_id=arm_id,
                action_type=action.action_type if arm_id is not None else None,
                discount_percent=(
                    float(action.discount_percent) if arm_id is not None and action.action_type == "discount" else None
                ),
                bundle_price_rupees=(
                    float(action.bundle_price) if arm_id is not None and action.action_type == "bundle_upsell" else None
                ),
                allowed_unmodified=unmodified_flag,
            )
        return offer

    def schedule_reasoning(
        self,
        state: CheckoutSessionState,
        action: ProposedAction,
        decision_context: DecisionContext,
        decision: "GateDecision",
        arm_id: str | None,
    ) -> None:
        """Schedule LLM reasoning as a background task (non-blocking).

        The buyer gets an immediate response; the reasoner runs async and
        updates state.reasoning_trace when done. Subsequent GETs on the session
        will surface the trace.
        """
        if self._reasoning is None:
            return
        bandit_action = None
        if arm_id is not None and action is not None:
            bandit_action = {
                "action_type": action.action_type,
                "arm_id": arm_id,
            }
            if action.action_type == "discount" and action.discount_percent is not None:
                bandit_action["discount_percent"] = float(action.discount_percent)
            elif action.action_type == "bundle_upsell":
                bandit_action["bundle_item"] = action.bundle_item
                bandit_action["bundle_price"] = float(action.bundle_price) if action.bundle_price else 0.0

        gate_decision = {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "final_action_type": decision.final_action.action_type if decision.final_action else None,
        }

        session_id = state.id
        target_sku = decision_context.target_sku
        item_category = decision_context.item_category
        cart_value_inr = decision_context.cart_value_inr
        buyer_allowance_inr = decision_context.buyer_allowance_inr
        is_stagnant = decision_context.is_stagnant or False
        days_in_stock = decision_context.days_in_stock
        reasoning = self._reasoning

        import threading
        import time as _time

        def _background():
            print(f"  [reasoner] START {session_id}", flush=True)
            try:
                from razorpay_agent.checkout.api import _event_bus
                _event_bus.put_nowait({
                    "type": "merchant_reasoning_start",
                    "agent": "MerchantAgent",
                    "message": f"🧠 Merchant reasoner started for {target_sku}",
                    "session_id": session_id,
                    "timestamp": datetime.now(UTC).isoformat(),
                })
            except Exception:
                pass
            
            try:
                t0 = _time.monotonic()
                result = reasoning.reason(
                    session_id,
                    target_sku=target_sku,
                    item_category=item_category,
                    cart_value_inr=cart_value_inr,
                    buyer_allowance_inr=buyer_allowance_inr,
                    is_stagnant=is_stagnant,
                    days_in_stock=days_in_stock,
                    bandit_action=bandit_action,
                    gate_decision=gate_decision,
                )
                elapsed = _time.monotonic() - t0
                print(f"  [reasoner] DONE {session_id} in {elapsed:.1f}s, {len(result.steps)} steps, trace set")
                
                ft = result.final_text.strip()
                low = ft.lower()
                if "verdict: approve" in low:
                    verdict = "APPROVE"
                elif "verdict: reject" in low:
                    verdict = "REJECT"
                elif "verdict: review" in low:
                    verdict = "REVIEW"
                else:
                    verdict = "REVIEW"
                
                state.reasoning_trace = {
                    "provider": result.provider,
                    "model": result.model,
                    "fallback": result.fallback,
                    "final_text": result.final_text,
                    "verdict": verdict,
                    "elapsed_seconds": elapsed,
                    "steps": [
                        {"step": s.step, "role": s.role, "content": s.content}
                        for s in result.steps
                    ],
                }
                
                try:
                    from razorpay_agent.checkout.api import _event_bus
                    _event_bus.put_nowait({
                        "type": "merchant_reasoning_done",
                        "agent": "MerchantAgent",
                        "message": f"✅ Merchant verdict: {verdict} ({elapsed:.1f}s)",
                        "session_id": session_id,
                        "verdict": verdict,
                        "final_text": result.final_text[:500],
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                except Exception:
                    pass
            except Exception as exc:
                print(f"  [reasoner] ERROR {session_id}: {type(exc).__name__}: {exc}", flush=True)
                state.reasoning_trace = {
                    "provider": "error",
                    "model": None,
                    "fallback": True,
                    "final_text": f"Reasoning unavailable: {type(exc).__name__}",
                    "verdict": "ERROR",
                    "steps": [],
                }
                try:
                    from razorpay_agent.checkout.api import _event_bus
                    _event_bus.put_nowait({
                        "type": "merchant_reasoning_done",
                        "agent": "MerchantAgent",
                        "message": f"❌ Merchant reasoner failed: {type(exc).__name__}",
                        "session_id": session_id,
                        "verdict": "ERROR",
                        "timestamp": datetime.now(UTC).isoformat(),
                    })
                except Exception:
                    pass

        t = threading.Thread(target=_background, daemon=True)
        t.name = f"reasoning-{session_id}"
        t.start()

    def resolve_accepted(self, state, paid_total_paise: int, base_total_paise: int) -> None:
        offer = state.applied_offer
        if offer is None or offer.gate_decision.allowed is False:
            return
        if state.is_stagnant:
            # Stagnant clearance objective (architecture.md §4.7/§4.8): clearing
            # the dead-stock unit earns the avoided carrying cost (relief); the
            # revenue the sale itself earns is credited separately above. A bundle
            # attachment and a discount sale are both valid ways to clear it.
            net_revenue_rupees = clearance_relief_rupees(
                base_total_paise / state.currency.minor_unit_divisor, state.days_in_stock
            )
        else:
            net_revenue_rupees = (
                paid_total_paise - EXPECTED_NO_OFFER_FRACTION * base_total_paise
            ) / state.currency.minor_unit_divisor
        self._append_resolution(
            state,
            ACCEPTED,
            f"buyer completed; paid {paid_total_paise / 100:.2f} vs base "
            f"{base_total_paise / 100:.2f}",
        )
        self._forward_reward(offer, net_revenue_rupees)

    def resolve_declined(self, state: CheckoutSessionState, why: str) -> None:
        offer = state.applied_offer
        if offer is None or offer.gate_decision.allowed is False:
            return
        self._append_resolution(state, DECLINED, why)
        reward = 0.0
        if state.is_stagnant:
            # A real stagnant proposal that did not clear incurs one period of
            # carrying cost. Guarded by _forward_reward's bandit-only check, so it
            # applies solely to observed outcomes, never hypothetical no-traffic.
            reward = -carrying_cost_penalty_rupees(offer.decision_context.cart_value_inr)
        self._forward_reward(offer, reward)

    def resolve_failed(self, state: CheckoutSessionState, why: str) -> None:
        offer = state.applied_offer
        if offer is None or offer.gate_decision.allowed is False:
            return
        self._append_resolution(state, FAILED, why)
        reward = 0.0
        if state.is_stagnant:
            reward = -carrying_cost_penalty_rupees(offer.decision_context.cart_value_inr)
        self._forward_reward(offer, reward)

    def _forward_reward(self, offer: AppliedOffer, reward: float) -> None:
        if not offer.bandit_proposed and offer.arm_id is None:
            return
        if self._watchdog is not None and offer.bandit_proposed:
            self._watchdog.observe_reward(reward)
        if self._decision_log is not None:
            self._decision_log.resolve_decision_reward(offer.decision_context.session_id, reward)
        if offer.arm_id is not None and self._policy is not None:
            self._policy.update(offer.arm_id, offer.decision_context, reward)

    def _append_resolution(
        self, state: CheckoutSessionState, status: str, detail: str
    ) -> None:
        offer = state.applied_offer
        assert offer is not None
        if offer.audit_entry_id is not None:
            self._audit.update_outcome(offer.audit_entry_id, status, detail)
            return
        self._audit.append(
            AuditEntry(
                timestamp=datetime.now(UTC),
                session_id=state.id,
                proposed_action=offer.proposed_action,
                gate_decision=offer.gate_decision,
                outcome=AuditOutcome(status, detail),
            )
        )

    def _fallback_action(self, session_id: str, cart_value_inr: float) -> ProposedAction:
        price_rupees = min(
            self._gate_config.fallback_bundle_price,
            self._gate_config.max_bundle_cart_share * cart_value_inr,
        )
        return ProposedAction(
            action_type="bundle_upsell",
            target=self._gate_config.fallback_bundle_item,
            expected_uplift=0.0,
            confidence=0.0,
            source=FALLBACK_SOURCE,
            session_id=session_id,
            bundle_item=self._gate_config.fallback_bundle_item,
            bundle_price=price_rupees,
        )

    @staticmethod
    def _discount_paise(final: ProposedAction, cart_value_paise: int) -> int:
        if final.action_type != "discount" or final.discount_percent is None:
            return 0
        return int(round(cart_value_paise * float(final.discount_percent) / 100.0))

    @staticmethod
    def _bundle_price_paise(final: ProposedAction) -> int:
        if final.action_type != "bundle_upsell" or final.bundle_price is None:
            return 0
        return int(round(float(final.bundle_price) * 100))


def _actions_equivalent(proposed: ProposedAction, final: ProposedAction) -> bool:
    if proposed.action_type != final.action_type:
        return False
    if proposed.action_type == "discount":
        return float(proposed.discount_percent) == float(final.discount_percent)
    return (
        proposed.bundle_item == final.bundle_item
        and float(proposed.bundle_price) == float(final.bundle_price)
    )
