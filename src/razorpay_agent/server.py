from __future__ import annotations

import os
import random
import sys
from pathlib import Path

from razorpay_agent.audit import AuditStore
from razorpay_agent.checkout.api import build_app
from razorpay_agent.checkout.catalog import DEMO_CATALOG
from razorpay_agent.checkout.inventory import InventoryStore
from razorpay_agent.checkout.offers import OfferPipeline
from razorpay_agent.checkout.payments import (
    PaymentProvider,
    RazorpayTestProvider,
    ScriptedPaymentProvider,
)
from razorpay_agent.checkout.sessions import SessionRepository
from razorpay_agent.decision import (
    BundleArm,
    ContextEncoder,
    DiscountArm,
    LinUCBPolicy,
)
from razorpay_agent.decision.co_purchase_graph import CoPurchaseGraph
from razorpay_agent.eval.storage import EvalStore
from razorpay_agent.gate import RulePolicyGateConfig
from razorpay_agent.reasoning.agent import ReasoningAgent
from razorpay_agent.reasoning.llm import resolve_provider
from razorpay_agent.reasoning.store import ReasoningStore
from razorpay_agent.reasoning.tools import ReasoningDeps

DEFAULT_DB_PATH = "demo/live.sqlite3"
ENV_PATH = ".env"
PRETRAINED_BANDIT_PATH = "demo/pretrained_bandit.json"
SABOTAGE_ENV_VAR = "RAZORPAY_AGENT_SABOTAGE_BANDIT"

DEFAULT_BASELINE_NET_REVENUE = 50.0
DEFAULT_BASELINE_COMPLIANCE = 0.85

LIVE_DISCOUNT_ARMS = (5.0, 10.0, 15.0, 20.0, 25.0, 35.0, 40.0)
LIVE_BUNDLE_ITEMS: dict[str, tuple[str, float]] = {
    "sku-socks": ("apparel", 499.0),
    "sku-mug": ("home", 599.0),
}
FALLBACK_ITEM = "sku-socks"


def load_env_file(path: str | Path = ENV_PATH) -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _credential(name: str, file_values: dict[str, str]) -> str | None:
    if name in file_values:
        return file_values[name]
    return os.environ.get(name)


def build_payment_provider(
    file_values: dict[str, str] | None = None,
) -> tuple[PaymentProvider, bool]:
    file_values = file_values if file_values is not None else load_env_file()
    
    # Only use live Razorpay if explicitly opted in
    use_live = os.environ.get("RAZORPAY_AGENT_USE_LIVE_PAYMENTS", "").strip() in ("1", "true", "yes")
    if not use_live:
        if file_values.get("RAZORPAY_KEY_ID"):
            print(
                "[razorpay-agent] Using SCRIPTED payment provider (set RAZORPAY_AGENT_USE_LIVE_PAYMENTS=1 for live Razorpay)",
                file=sys.stderr,
            )
        return ScriptedPaymentProvider(), False
    
    key_id = _credential("RAZORPAY_KEY_ID", file_values)
    key_secret = _credential("RAZORPAY_KEY_SECRET", file_values)
    if key_id and key_secret:
        return RazorpayTestProvider(key_id, key_secret), True
    print(
        "[razorpay-agent] RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not found in .env "
        "or environment; using SCRIPTED payment provider",
        file=sys.stderr,
    )
    return ScriptedPaymentProvider(), False


def build_policy(pretrained_path: str | Path | None = PRETRAINED_BANDIT_PATH):
    categories = tuple(sorted({product.category for product in DEMO_CATALOG}))
    if pretrained_path is not None and Path(pretrained_path).exists():
        from razorpay_agent.decision.linucb import LinUCBPolicy

        policy = LinUCBPolicy.load(pretrained_path)
        loaded_categories = tuple(sorted(policy_encoder_categories(policy)))
        if loaded_categories != categories:
            print(
                f"[razorpay-agent] pretrained bandit categories {loaded_categories} do not "
                f"match catalog {categories}; starting COLD",
                file=sys.stderr,
            )
            return fresh_policy(categories), False
        print(
            f"[razorpay-agent] warm-starting bandit from {pretrained_path} "
            f"({getattr(policy, '_trained_sessions', 0)} prior updates)",
        )
        return policy, True

    print(
        f"[razorpay-agent] no pretrained bandit at {pretrained_path} — starting COLD "
        "(run demo/pretrain_bandit.py to warm-start)",
        file=sys.stderr,
    )
    return fresh_policy(categories), False


def policy_encoder_categories(policy):
    return policy._encoder.categories


def fresh_policy(categories):
    arms = [
        DiscountArm(f"d{int(percent)}", percent) for percent in LIVE_DISCOUNT_ARMS
    ] + [
        BundleArm(f"b_{item}", item, price)
        for item, (_, price) in LIVE_BUNDLE_ITEMS.items()
    ]
    return LinUCBPolicy(arms, ContextEncoder(categories), alpha=0.5)


def build_watchdog(db_path: str | Path = DEFAULT_DB_PATH):

    from razorpay_agent.eval.report import latest_report
    from razorpay_agent.eval.storage import EvalStore
    from razorpay_agent.watchdog.storage import SystemEventStore
    from razorpay_agent.watchdog.watchdog import (
        DEFAULT_COMPLIANCE_TRIGGER_FRACTION,
        DEFAULT_REVENUE_TRIGGER_FRACTION,
        SafetyWatchdog,
    )

    eval_store = EvalStore(db_path)
    report = latest_report(eval_store)
    eval_store.close()

    if report is not None:
        metrics = report["metrics"]
        baseline_revenue = metrics["uplift_over_baseline"]["bandit_mean_net_revenue"]
        baseline_compliance = metrics["gate_compliance_rate"]
        print(
            f"[watchdog] baselines loaded from offline validation run #{report['run_id']}: "
            f"{baseline_revenue:.2f} net-revenue/decision, "
            f"{baseline_compliance:.1%} gate compliance"
        )
    else:
        baseline_revenue = DEFAULT_BASELINE_NET_REVENUE
        baseline_compliance = DEFAULT_BASELINE_COMPLIANCE
        print(
            "[watchdog] no offline validation run found; using conservative defaults: "
            f"{baseline_revenue} net-revenue/decision, {baseline_compliance:.0%} compliance",
            file=sys.stderr,
        )

    events = SystemEventStore(db_path)

    def _record_demotion(reason: str) -> None:
        events.record(
            component="watchdog",
            event_type="demotion",
            detail={"reason": reason},
        )

    watchdog = SafetyWatchdog(
        baseline_net_revenue_per_decision=baseline_revenue,
        baseline_gate_compliance_rate=baseline_compliance,
        revenue_trigger_fraction=DEFAULT_REVENUE_TRIGGER_FRACTION,
        compliance_trigger_fraction=DEFAULT_COMPLIANCE_TRIGGER_FRACTION,
        on_demote=_record_demotion,
    )
    return watchdog, events


def maybe_sabotage(policy):
    import os

    if os.environ.get(SABOTAGE_ENV_VAR, "").strip() not in ("1", "true", "yes"):
        return policy
    from razorpay_agent.watchdog.sabotage import SabotagedPolicy

    bad_arm = "b_sku-charger"
    if hasattr(policy, "_arms") and bad_arm in policy._arms:
        print(
            f"[razorpay-agent] SABOTAGE MODE ON: decision layer will always propose "
            f"'{bad_arm}' (a deterministically terrible arm) for watchdog demos",
            file=sys.stderr,
        )
        return SabotagedPolicy(policy, bad_arm)
    print(
        "[razorpay-agent] SABOTAGE_MODE requested but no wrappable policy; ignoring",
        file=sys.stderr,
    )
    return policy


def build_live_app(
    db_path: str | Path = DEFAULT_DB_PATH,
    pretrained_bandit_path: str | Path | None = PRETRAINED_BANDIT_PATH,
    temperature: float = 0.0,
    rng: random.Random | None = None,
):
    return _build_app_common(
        db_path=db_path,
        pretrained_bandit_path=pretrained_bandit_path,
        temperature=temperature,
        rng=rng,
        use_db=True,
    )


def build_serverless_app(
    pretrained_bandit_path: str | Path | None = PRETRAINED_BANDIT_PATH,
    temperature: float = 0.0,
    rng: random.Random | None = None,
    byok_store: dict[str, dict] | None = None,
) -> tuple[FastAPI, Repository, AuditStore]:
    """Build app with in-memory stores for Vercel serverless."""
    return _build_app_common(
        db_path=None,
        pretrained_bandit_path=pretrained_bandit_path,
        temperature=temperature,
        rng=rng,
        use_db=False,
        byok_store=byok_store or {},
    )


def _build_app_common(
    db_path: str | Path | None,
    pretrained_bandit_path: str | Path | None = PRETRAINED_BANDIT_PATH,
    temperature: float = 0.0,
    rng: random.Random | None = None,
    use_db: bool = True,
):
    provider, is_live = build_payment_provider()

    policy, is_warm = build_policy(pretrained_bandit_path)
    policy = maybe_sabotage(policy)

    watchdog = None
    if use_db and db_path is not None:
        watchdog, _ = build_watchdog(db_path)

    gate_config = RulePolicyGateConfig(
        fallback_bundle_item=FALLBACK_ITEM,
        fallback_bundle_price=LIVE_BUNDLE_ITEMS[FALLBACK_ITEM][1],
    )

    if use_db and db_path is not None:
        audit_store = AuditStore(db_path)
        eval_store = EvalStore(db_path)
        reasoning_store = ReasoningStore(db_path)
    else:
        audit_store = AuditStore(":memory:")
        eval_store = EvalStore(":memory:")
        reasoning_store = ReasoningStore(":memory:")

    # Build the LLM reasoner: resolves provider from .env (nous/openai/anthropic/tencent/stub).
    # Falls back to StubBackend if no key or SDK available — pipeline always runs.
    llm_backend = resolve_provider()
    print(
        f"[razorpay-agent] LLM reasoner: {llm_backend.name}/{llm_backend.model}"
    )

    reasoning_deps = ReasoningDeps(
        catalog=DEMO_CATALOG,
        policy=policy,
        gate_config=gate_config,
        regimen_graph=CoPurchaseGraph.from_catalog(DEMO_CATALOG),
    )
    reasoning_agent = ReasoningAgent(
        llm=llm_backend,
        deps=reasoning_deps,
        store=reasoning_store,
    )

    pipeline = OfferPipeline(
        policy,
        gate_config,
        audit_store,
        watchdog=watchdog,
        decision_log=eval_store,
        temperature=temperature,
        rng=rng,
        reasoning_agent=reasoning_agent,
    )
    pipeline.attach_graph()

    inventory = InventoryStore.from_catalog(DEMO_CATALOG)

    app = build_app(
        DEMO_CATALOG,
        SessionRepository(),
        pipeline,
        provider,
        eval_store=eval_store,
        watchdog=watchdog,
        inventory=inventory,
        audit_store=audit_store,
    )
    app.state.bandit_warm = is_warm
    app.state.watchdog = watchdog
    app.state.pipeline = pipeline
    return app, audit_store, is_live
