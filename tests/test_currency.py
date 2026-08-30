from razorpay_agent.checkout.sessions import to_paise, to_rupees
from razorpay_agent.core.currency import (
    INR,
    JPY,
    KWD,
    Currency,
    resolve_currency,
)
from razorpay_agent.core.errors import ContractViolation


def test_inr_round_trips_at_100():
    assert to_paise(2499.0, INR) == 249900
    assert to_rupees(249900, INR) == 2499.0


def test_jpy_has_no_minor_unit():
    # 1000 yen is 1000 minor units (no paise equivalent)
    assert to_paise(1000.0, JPY) == 1000
    assert to_rupees(1000, JPY) == 1000.0


def test_kwd_uses_1000_divisor():
    assert to_paise(1.5, KWD) == 1500


def test_resolve_currency_is_case_insensitive():
    assert resolve_currency("INR") is INR
    assert resolve_currency("jpy") is JPY


def test_unknown_currency_rejected():
    try:
        resolve_currency("xyz")
    except ContractViolation:
        pass
    else:
        raise AssertionError("expected ContractViolation for unknown currency")


def test_currency_requires_positive_divisor():
    try:
        Currency("bad", 0)
    except ContractViolation:
        pass
    else:
        raise AssertionError("expected ContractViolation for zero divisor")
