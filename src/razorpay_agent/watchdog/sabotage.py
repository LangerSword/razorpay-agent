from __future__ import annotations

from razorpay_agent.decision.context import DecisionContext
from razorpay_agent.decision.linucb import LinUCBPolicy


class SabotagedPolicy:
    """Deterministically bad decision layer for watchdog demos.

    Wraps a real policy but always proposes one deliberately terrible arm:
    an oversized bundle the gate rejects in nearly every context, driving both
    realized net revenue and gate compliance toward zero on a fixed schedule.
    """

    def __init__(self, wrapped: LinUCBPolicy, bad_arm_id: str) -> None:
        if bad_arm_id not in wrapped.arm_ids:
            raise ValueError(f"arm {bad_arm_id!r} is not part of the wrapped policy")
        self._wrapped = wrapped
        self._bad_arm_id = bad_arm_id

    @property
    def arm_ids(self):
        return self._wrapped.arm_ids

    def propose_with_arm(self, context: DecisionContext):
        arm = self._wrapped._arms[self._bad_arm_id]
        action = self._wrapped._to_action(arm, context, expected_uplift=0.0, confidence=1.0)
        return self._bad_arm_id, action

    def update(self, arm_id: str, context: DecisionContext, reward: float) -> None:
        self._wrapped.update(arm_id, context, reward)
