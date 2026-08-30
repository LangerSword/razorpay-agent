from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from razorpay_agent.core.actions import ProposedAction
from razorpay_agent.decision.arms import Arm, BundleArm, DiscountArm
from razorpay_agent.decision.context import ContextEncoder, DecisionContext

BANDIT_SOURCE = "linucb_bandit"

STATE_FORMAT_VERSION = 1


class LinUCBPolicy:
    def __init__(
        self,
        arms: tuple[Arm, ...] | list[Arm],
        encoder: ContextEncoder,
        alpha: float = 1.0,
    ) -> None:
        if not arms:
            raise ValueError("at least one arm is required")
        arm_ids = [arm.arm_id for arm in arms]
        if len(set(arm_ids)) != len(arm_ids):
            raise ValueError("arm ids must be unique")
        if isinstance(alpha, bool) or not math.isfinite(float(alpha)) or float(alpha) <= 0.0:
            raise ValueError("alpha must be a finite positive number")

        self._arms: dict[str, Arm] = {arm.arm_id: arm for arm in arms}
        self._order = list(arm_ids)
        self._encoder = encoder
        self._alpha = float(alpha)
        self._A = {arm_id: np.eye(encoder.dimension) for arm_id in arm_ids}
        self._b = {arm_id: np.zeros(encoder.dimension) for arm_id in arm_ids}
        self._trained_sessions = 0

    @property
    def arm_ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    def scores(self, context: DecisionContext) -> dict[str, float]:
        features = self._encoder.encode(context)
        return {
            arm_id: sum(self._score(arm_id, features))
            for arm_id in self._order
        }

    def propose(self, context: DecisionContext) -> ProposedAction | None:
        arm_id, action = self.propose_with_arm(context)
        return action

    def propose_with_arm(self, context: DecisionContext) -> tuple[str | None, ProposedAction | None]:
        features = self._encoder.encode(context)

        best_arm_id: str | None = None
        best_score = -math.inf
        best_expected = 0.0
        best_bonus = 0.0
        for arm_id in self._order:
            expected, bonus = self._score(arm_id, features)
            score = expected + bonus
            if score > best_score:
                best_arm_id, best_score = arm_id, score
                best_expected, best_bonus = expected, bonus

        if best_arm_id is None or best_score <= 0.0:
            return None, None

        confidence = abs(best_expected) / (abs(best_expected) + best_bonus)
        return (
            best_arm_id,
            self._to_action(self._arms[best_arm_id], context, best_expected, confidence),
        )

    def update(self, arm_id: str, context: DecisionContext, reward: float) -> None:
        if arm_id not in self._arms:
            raise ValueError(f"unknown arm {arm_id!r}")
        if isinstance(reward, bool) or not isinstance(reward, (int, float)):
            raise ValueError("reward must be a number")
        reward = float(reward)
        if not math.isfinite(reward):
            raise ValueError("reward must be finite")

        features = self._encoder.encode(context)
        self._A[arm_id] += np.outer(features, features)
        self._b[arm_id] += reward * features
        self._trained_sessions += 1

    def _score(self, arm_id: str, features: np.ndarray) -> tuple[float, float]:
        theta = np.linalg.solve(self._A[arm_id], self._b[arm_id])
        expected = float(theta @ features)
        covariance_term = float(features @ np.linalg.solve(self._A[arm_id], features))
        bonus = self._alpha * math.sqrt(max(covariance_term, 0.0))
        return expected, bonus

    def to_state_dict(self) -> dict:
        return {
            "format_version": STATE_FORMAT_VERSION,
            "alpha": self._alpha,
            "categories": list(self._encoder.categories),
            "trained_sessions": getattr(self, "_trained_sessions", 0),
            "arms": [self._arm_record(self._arms[arm_id]) for arm_id in self._order],
            "A": {
                arm_id: np.asarray(matrix).tolist()
                for arm_id, matrix in self._A.items()
            },
            "b": {
                arm_id: np.asarray(vector).tolist()
                for arm_id, vector in self._b.items()
            },
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_state_dict()))

    @classmethod
    def load(cls, path: str | Path) -> LinUCBPolicy:
        state = json.loads(Path(path).read_text())
        return cls.from_state_dict(state)

    @classmethod
    def from_state_dict(cls, state: dict) -> LinUCBPolicy:
        if state.get("format_version") != STATE_FORMAT_VERSION:
            raise ValueError(
                f"unsupported bandit state format {state.get('format_version')!r}"
            )
        arms = [cls._arm_from_record(record) for record in state["arms"]]
        encoder = ContextEncoder(tuple(state["categories"]))
        policy = cls(arms, encoder, alpha=float(state["alpha"]))
        dimension = encoder.dimension

        for arm_id in state["A"]:
            if arm_id not in policy._A:
                raise ValueError(f"state contains unknown arm {arm_id!r}")
        for arm_id in policy.arm_ids:
            A = np.asarray(state["A"][arm_id], dtype=float)
            b = np.asarray(state["b"][arm_id], dtype=float)
            if A.shape != (dimension, dimension):
                raise ValueError(f"arm {arm_id}: A has shape {A.shape}, expected {(dimension, dimension)}")
            if b.shape != (dimension,):
                raise ValueError(f"arm {arm_id}: b has shape {b.shape}, expected {(dimension,)}")
            if not np.allclose(A, A.T):
                raise ValueError(f"arm {arm_id}: A is not symmetric")
            policy._A[arm_id] = A
            policy._b[arm_id] = b

        policy._trained_sessions = int(state.get("trained_sessions", 0))
        return policy

    @staticmethod
    def _arm_record(arm: Arm) -> dict:
        if isinstance(arm, DiscountArm):
            return {"arm_id": arm.arm_id, "kind": "discount", "discount_percent": arm.discount_percent}
        return {
            "arm_id": arm.arm_id,
            "kind": "bundle",
            "bundle_item": arm.bundle_item,
            "bundle_price": arm.bundle_price,
        }

    @staticmethod
    def _arm_from_record(record: dict) -> Arm:
        if record["kind"] == "discount":
            return DiscountArm(record["arm_id"], float(record["discount_percent"]))
        return BundleArm(record["arm_id"], record["bundle_item"], float(record["bundle_price"]))

    def _to_action(
        self,
        arm: Arm,
        context: DecisionContext,
        expected_uplift: float,
        confidence: float,
    ) -> ProposedAction:
        if isinstance(arm, DiscountArm):
            return ProposedAction(
                action_type="discount",
                target=context.target_sku,
                expected_uplift=expected_uplift,
                confidence=confidence,
                source=BANDIT_SOURCE,
                session_id=context.session_id,
                discount_percent=float(arm.discount_percent),
            )
        if isinstance(arm, BundleArm):
            return ProposedAction(
                action_type="bundle_upsell",
                target=arm.bundle_item,
                expected_uplift=expected_uplift,
                confidence=confidence,
                source=BANDIT_SOURCE,
                session_id=context.session_id,
                bundle_item=arm.bundle_item,
                bundle_price=float(arm.bundle_price),
            )
        raise TypeError(f"unsupported arm type {type(arm).__name__}")
