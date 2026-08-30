from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

DEFAULT_MIN_SAMPLE = 30
DEFAULT_REVENUE_TRIGGER_FRACTION = 0.5
DEFAULT_COMPLIANCE_TRIGGER_FRACTION = 0.7
DEFAULT_WINDOW_SIZE = 100


class SafetyWatchdog:
    def __init__(
        self,
        baseline_net_revenue_per_decision: float,
        baseline_gate_compliance_rate: float,
        min_sample: int = DEFAULT_MIN_SAMPLE,
        revenue_trigger_fraction: float = DEFAULT_REVENUE_TRIGGER_FRACTION,
        compliance_trigger_fraction: float = DEFAULT_COMPLIANCE_TRIGGER_FRACTION,
        window_size: int = DEFAULT_WINDOW_SIZE,
        on_demote: Callable[[str], None] | None = None,
    ) -> None:
        for value in (baseline_net_revenue_per_decision, baseline_gate_compliance_rate):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError("baselines must be finite numbers")
        if baseline_net_revenue_per_decision <= 0.0:
            raise ValueError("baseline net revenue must be positive")
        if not 0.0 < baseline_gate_compliance_rate <= 1.0:
            raise ValueError("baseline compliance must lie in (0, 1]")
        if min_sample < 1:
            raise ValueError("min_sample must be at least 1")

        self._baseline_revenue = float(baseline_net_revenue_per_decision)
        self._baseline_compliance = float(baseline_gate_compliance_rate)
        self._min_sample = int(min_sample)
        self._revenue_trigger_fraction = float(revenue_trigger_fraction)
        self._compliance_trigger_fraction = float(compliance_trigger_fraction)

        self._rewards: deque[float] = deque(maxlen=int(window_size))
        self._compliance: deque[bool] = deque(maxlen=int(window_size))
        self._demoted = False
        self._demotion_reason: str | None = None
        self._demoted_at: str | None = None
        self._on_demote = on_demote

    @property
    def demoted(self) -> bool:
        return self._demoted

    @property
    def demotion_reason(self) -> str | None:
        return self._demotion_reason

    def observe_gate_outcome(self, outcome: bool | None) -> None:
        """None means the bandit abstained; True/False means a raw proposal passed
        the gate unmodified or did not (capped or rejected)."""
        if self._demoted:
            return
        if outcome is not None:
            self._compliance.append(bool(outcome))
        self._check()

    def observe_reward(self, reward: float) -> None:
        if self._demoted:
            return
        self._rewards.append(float(reward))
        self._check()

    def _check(self) -> None:
        if self._demoted:
            return
        reasons: list[str] = []

        if len(self._rewards) >= self._min_sample:
            mean_revenue = sum(self._rewards) / len(self._rewards)
            if mean_revenue < self._baseline_revenue * self._revenue_trigger_fraction:
                reasons.append(
                    f"rolling net revenue {mean_revenue:.2f}/decision fell below "
                    f"{self._revenue_trigger_fraction:.0%} of offline baseline "
                    f"{self._baseline_revenue:.2f} over {len(self._rewards)} decisions"
                )

        if len(self._compliance) >= self._min_sample:
            compliance_rate = sum(self._compliance) / len(self._compliance)
            if compliance_rate < self._baseline_compliance * self._compliance_trigger_fraction:
                reasons.append(
                    f"rolling gate compliance {compliance_rate:.1%} fell below "
                    f"{self._compliance_trigger_fraction:.0%} of offline baseline "
                    f"{self._baseline_compliance:.1%} over {len(self._compliance)} proposals"
                )

        if reasons:
            self.demote("; ".join(reasons))

    def demote(self, reason: str) -> None:
        if self._demoted:
            return
        self._demoted = True
        self._demotion_reason = reason
        self._demoted_at = datetime.now(UTC).isoformat()
        print(
            f"[watchdog] DEMOTING decision layer to rule-only fallback: {reason}",
        )
        if self._on_demote is not None:
            self._on_demote(reason)

    def promote(self, operator_note: str) -> None:
        if not self._demoted:
            raise RuntimeError("watchdog is not demoted; nothing to promote")
        if not operator_note.strip():
            raise ValueError("an operator note is required to re-promote the bandit")
        print(
            f"[watchdog] RE-PROMOTING decision layer to bandit "
            f"(operator note: {operator_note})"
        )
        self._demoted = False
        self._demotion_reason = None
        self._demoted_at = None
        self._rewards.clear()
        self._compliance.clear()

    def status(self) -> dict:
        rewards = list(self._rewards)
        compliance = list(self._compliance)
        return {
            "demoted": self._demoted,
            "demotion_reason": self._demotion_reason,
            "demoted_at": self._demoted_at,
            "baseline": {
                "net_revenue_per_decision": self._baseline_revenue,
                "gate_compliance_rate": self._baseline_compliance,
            },
            "thresholds": {
                "min_sample": self._min_sample,
                "revenue_trigger_fraction": self._revenue_trigger_fraction,
                "compliance_trigger_fraction": self._compliance_trigger_fraction,
            },
            "rolling": {
                "reward_samples": len(rewards),
                "mean_net_revenue": sum(rewards) / len(rewards) if rewards else None,
                "compliance_samples": len(compliance),
                "compliance_rate": (
                    sum(compliance) / len(compliance) if compliance else None
                ),
            },
        }
