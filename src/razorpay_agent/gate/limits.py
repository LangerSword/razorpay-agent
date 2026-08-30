from razorpay_agent.core.actions import BUNDLE_UPSELL

FALLBACK_SOURCE = "fallback_rule"

ONE_OFFER_PER_SESSION = "one_offer_per_session"
MAX_DISCOUNT_PCT = "max_discount_pct"
MAX_DISCOUNT_RUPEE_CAP = "max_discount_rupee_cap"
MAX_BUNDLE_SHARE = "max_bundle_share"
BUYER_ALLOWANCE = "buyer_allowance"

__all__ = [
    "BUNDLE_UPSELL",
    "BUYER_ALLOWANCE",
    "FALLBACK_SOURCE",
    "MAX_BUNDLE_SHARE",
    "MAX_DISCOUNT_PCT",
    "MAX_DISCOUNT_RUPEE_CAP",
    "ONE_OFFER_PER_SESSION",
]
