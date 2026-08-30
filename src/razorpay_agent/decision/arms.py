from __future__ import annotations

import math
from dataclasses import dataclass

DISCOUNT_KIND = "discount"
BUNDLE_UPSELL_KIND = "bundle_upsell"


@dataclass(frozen=True)
class DiscountArm:
    arm_id: str
    discount_percent: float

    def __post_init__(self) -> None:
        _require_arm_id(self.arm_id)
        if (
            isinstance(self.discount_percent, bool)
            or not isinstance(self.discount_percent, (int, float))
            or not math.isfinite(float(self.discount_percent))
            or not 0.0 < float(self.discount_percent) <= 100.0
        ):
            raise ValueError("discount_percent must lie within (0, 100]")

    @property
    def kind(self) -> str:
        return DISCOUNT_KIND


@dataclass(frozen=True)
class BundleArm:
    arm_id: str
    bundle_item: str
    bundle_price: float

    def __post_init__(self) -> None:
        _require_arm_id(self.arm_id)
        if not isinstance(self.bundle_item, str) or not self.bundle_item.strip():
            raise ValueError("bundle_item must be a non-empty string")
        if (
            isinstance(self.bundle_price, bool)
            or not isinstance(self.bundle_price, (int, float))
            or not math.isfinite(float(self.bundle_price))
            or float(self.bundle_price) <= 0.0
        ):
            raise ValueError("bundle_price must be a positive number")

    @property
    def kind(self) -> str:
        return BUNDLE_UPSELL_KIND


Arm = DiscountArm | BundleArm


def _require_arm_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("arm_id must be a non-empty string")
