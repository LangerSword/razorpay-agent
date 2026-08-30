from __future__ import annotations

from dataclasses import dataclass

from razorpay_agent.core.errors import ContractViolation


@dataclass(frozen=True)
class Currency:
    """A currency and its minor-unit divisor.

    Money in this system is stored as integer minor units (e.g. paise). The
    divisor maps major units (rupees, dollars, yen) to those minor units. INR
    uses 100 (1 rupee = 100 paise); JPY uses 1 (no minor unit); KWD uses 1000.

    This exists so multi-currency support is a configuration change, not a
    rewrite: carry a ``Currency`` on the checkout session and pass it to
    ``to_paise`` / ``to_rupees``. The default everywhere is INR.
    """

    code: str
    minor_unit_divisor: int

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ContractViolation("currency code must be a non-empty string")
        if (
            isinstance(self.minor_unit_divisor, bool)
            or not isinstance(self.minor_unit_divisor, int)
            or self.minor_unit_divisor <= 0
        ):
            raise ContractViolation("minor_unit_divisor must be a positive integer")

    def to_minor(self, major: float) -> int:
        return int(round(major * self.minor_unit_divisor))

    def to_major(self, minor: int) -> float:
        return minor / self.minor_unit_divisor


INR = Currency("inr", 100)
USD = Currency("usd", 100)
JPY = Currency("jpy", 1)
KWD = Currency("kwd", 1000)

_KNOWN: dict[str, Currency] = {c.code: c for c in (INR, USD, JPY, KWD)}


def resolve_currency(code: str) -> Currency:
    code = (code or "").strip().lower()
    if code not in _KNOWN:
        raise ContractViolation(f"unsupported currency {code!r}")
    return _KNOWN[code]
