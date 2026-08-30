from __future__ import annotations

import math
from dataclasses import dataclass, replace

from razorpay_agent.core.actions import BUNDLE_UPSELL, DISCOUNT, ProposedAction
from razorpay_agent.core.decisions import GateDecision
from razorpay_agent.gate.context import InvalidContext, SessionContext
from razorpay_agent.gate.limits import (
    BUYER_ALLOWANCE,
    FALLBACK_SOURCE,
    MAX_BUNDLE_SHARE,
    MAX_DISCOUNT_PCT,
    MAX_DISCOUNT_RUPEE_CAP,
    ONE_OFFER_PER_SESSION,
)


@dataclass(frozen=True)
class RulePolicyGateConfig:
    fallback_bundle_item: str
    fallback_bundle_price: float
    max_discount_percent: float = 15.0
    max_discount_rupee_cap: float = 300.0
    max_bundle_cart_share: float = 0.20

    def __post_init__(self) -> None:
        if not isinstance(self.fallback_bundle_item, str) or not self.fallback_bundle_item.strip():
            raise InvalidContext("fallback_bundle_item must be a non-empty string")
        for field in (
            "fallback_bundle_price",
            "max_discount_percent",
            "max_discount_rupee_cap",
            "max_bundle_cart_share",
        ):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise InvalidContext(f"{field} must be a number")
            number = float(value)
            if not math.isfinite(number) or number <= 0.0:
                raise InvalidContext(f"{field} must be a finite positive number")
        if not 0.0 < float(self.max_bundle_cart_share) < 1.0:
            raise InvalidContext("max_bundle_cart_share must lie strictly between 0 and 1")


class RulePolicyGate:
    def __init__(self, config: RulePolicyGateConfig) -> None:
        self._config = config

    def evaluate(self, action: ProposedAction, context: SessionContext) -> GateDecision:
        if action.session_id != context.session_id:
            raise InvalidContext(
                "action session_id does not match the session context being evaluated"
            )

        checked: list[str] = []
        if context.already_offered:
            checked.append(ONE_OFFER_PER_SESSION)
            return self._reject(
                action,
                context,
                checked,
                "session already received its single offer; no second offer goes out",
            )

        if action.action_type == DISCOUNT:
            return self._evaluate_discount(action, context, checked)
        return self._evaluate_bundle(action, context, checked)

    def _evaluate_discount(
        self, action: ProposedAction, context: SessionContext, checked: list[str]
    ) -> GateDecision:
        proposed_percent = float(action.discount_percent)

        checked.append(MAX_DISCOUNT_PCT)
        effective_percent = min(proposed_percent, self._config.max_discount_percent)
        capped_by_percent = effective_percent < proposed_percent

        checked.append(MAX_DISCOUNT_RUPEE_CAP)
        rupee_amount = context.cart_value_inr * effective_percent / 100.0
        capped_by_rupee = False
        if rupee_amount > self._config.max_discount_rupee_cap:
            implied_percent = (
                100.0 * self._config.max_discount_rupee_cap / context.cart_value_inr
            )
            effective_percent = math.floor(implied_percent * 100.0) / 100.0
            capped_by_rupee = True

        if effective_percent <= 0.0:
            return self._reject(
                action,
                context,
                checked,
                "discount limits leave no meaningful discount on this cart",
            )

        final_action = replace(action, discount_percent=effective_percent)

        checked.append(BUYER_ALLOWANCE)
        projected_total = context.cart_value_inr * (1.0 - effective_percent / 100.0)
        if projected_total > context.buyer_allowance_inr:
            return self._reject(
                action,
                context,
                checked,
                f"projected total {projected_total:.2f} exceeds buyer allowance "
                f"{context.buyer_allowance_inr:.2f}",
            )

        if capped_by_rupee:
            reason = (
                f"rupee discount capped at {self._config.max_discount_rupee_cap:.2f} "
                f"({effective_percent:g}% of cart)"
            )
        elif capped_by_percent:
            reason = (
                f"proposed {proposed_percent:g}% discount capped to "
                f"{self._config.max_discount_percent:g}%"
            )
        else:
            reason = f"{effective_percent:g}% discount within all limits"
        return GateDecision(True, tuple(checked), reason, final_action)

    def _evaluate_bundle(
        self, action: ProposedAction, context: SessionContext, checked: list[str]
    ) -> GateDecision:
        bundle_price = float(action.bundle_price)

        checked.append(MAX_BUNDLE_SHARE)
        max_price = self._config.max_bundle_cart_share * context.cart_value_inr
        if bundle_price > max_price:
            return self._reject(
                action,
                context,
                checked,
                f"bundle price {bundle_price:.2f} exceeds "
                f"{self._config.max_bundle_cart_share:.0%} of cart value",
            )

        checked.append(BUYER_ALLOWANCE)
        projected_total = context.cart_value_inr + bundle_price
        if projected_total > context.buyer_allowance_inr:
            return self._reject(
                action,
                context,
                checked,
                f"projected total {projected_total:.2f} exceeds buyer allowance "
                f"{context.buyer_allowance_inr:.2f}",
            )

        return GateDecision(
            True, tuple(checked), "bundle within share limit and buyer allowance", action
        )

    def _reject(
        self,
        action: ProposedAction,
        context: SessionContext,
        checked: list[str],
        why: str,
    ) -> GateDecision:
        fallback = self._fallback_action(context)
        return GateDecision(False, tuple(checked), f"rejected: {why}; no offer goes out", fallback)

    def _fallback_action(self, context: SessionContext) -> ProposedAction:
        price = min(
            self._config.fallback_bundle_price,
            self._config.max_bundle_cart_share * context.cart_value_inr,
        )
        return ProposedAction(
            action_type=BUNDLE_UPSELL,
            target=self._config.fallback_bundle_item,
            expected_uplift=0.0,
            confidence=0.0,
            source=FALLBACK_SOURCE,
            session_id=context.session_id,
            bundle_item=self._config.fallback_bundle_item,
            bundle_price=price,
        )
