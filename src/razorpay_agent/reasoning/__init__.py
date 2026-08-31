"""reasoning — Hermes-style LLM reasoner for the MerchantAgent.

The reasoner explains *why* the decision layer proposed an offer. It is strictly
advisory: it reads context through registered tools and writes a reasoning trace to
``reasoning_log``. It never proposes or executes a settlement — money execution
stays in the non-LLM bandit + gate path.
"""
