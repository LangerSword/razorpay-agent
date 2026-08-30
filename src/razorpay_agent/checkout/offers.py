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
from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy
from razorpay_agent.eval.synthetic import AVG_BASE_COMPLETION_PROB
from razorpay_agent.gate.context import SessionContext
from razorpay_agent.gate.gate import RulePolicyGate, RulePolicyGateConfig

FALLBACK_SOURCE = "fallback_rule"

EXPECTED_NO_OFFER_FRACTION = AVG_BASE_COMPLETION_PROB


class OfferPipeline:
    def __init__(
        self,
        policy: LinUCBPolicy | None,
        gate_config: RulePolicyGateConfig,
        audit_store: AuditStore,
        watchdog=None,
        decision_log=None,
    ) -> None:
        self._policy = policy
        self._gate = RulePolicyGate(gate_config)
        self._gate_config = gate_config
        self._audit = audit_store
        self._watchdog = watchdog
        self._decision_log = decision_log

    def propose_for_session(
        self,
        state: CheckoutSessionState,
        cart_value_paise: int,
        target_sku: str,
        category: str,
    ) -> AppliedOffer | None:
        if state.applied_offer is not None:
            return state.applied_offer

        decision_context = DecisionContext(
            session_id=state.id,
            target_sku=target_sku,
            item_category=category,
            cart_value_inr=to_rupees(cart_value_paise, state.currency),
            buyer_allowance_inr=to_rupees(state.allowance_max_paise, state.currency),
        )

        bandit_consulted = self._policy is not None and not (
            self._watchdog is not None and self._watchdog.demoted
        )
        arm_id: str | None = None
        action: ProposedAction | None = None
        if bandit_consulted:
            arm_id, action = self._policy.propose_with_arm(decision_context)
        if action is None:
            action = self._fallback_action(state.id, to_rupees(cart_value_paise, state.currency))
            arm_id = None

        gate_context = SessionContext(
            session_id=state.id,
            cart_value_inr=to_rupees(cart_value_paise, state.currency),
            buyer_allowance_inr=to_rupees(state.allowance_max_paise, state.currency),
            already_offered=False,
        )
        decision = self._gate.evaluate(action, gate_context)

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
        self._forward_reward(offer, 0.0)

    def resolve_failed(self, state: CheckoutSessionState, why: str) -> None:
        offer = state.applied_offer
        if offer is None or offer.gate_decision.allowed is False:
            return
        self._append_resolution(state, FAILED, why)
        self._forward_reward(offer, 0.0)

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
