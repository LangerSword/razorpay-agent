from __future__ import annotations

from datetime import UTC, datetime

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
            return self._graph.propose_for_session(state, cart_value_paise, target_sku, category)

        decision_context = self._step_context(state, cart_value_paise, target_sku, category)
        arm_id, action, bandit_consulted = self._step_consult(
            state, decision_context, cart_value_paise
        )
        decision = self._step_gate(state, action, cart_value_paise)
        return self._step_finalize(
            state, action, decision_context, decision, arm_id, bandit_consulted,
            cart_value_paise, target_sku, category,
        )

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
