import random

from fastapi.testclient import TestClient

from razorpay_agent.checkout import (
    DEMO_CATALOG,
    OfferPipeline,
    ScriptedPaymentProvider,
    SessionRepository,
    build_app,
)
from razorpay_agent.eval import EvalStore, latest_report, run_offline_validation
from razorpay_agent.eval.synthetic import (
    BUNDLE_IRRELEVANT_TAKE_RATE,
    BUNDLE_RELEVANT_TAKE_RATE,
    SimOffer,
    SimSession,
    SimulatedBuyerModel,
    bundle_outcome_probs,
    discount_completion_prob,
    expected_net_revenue,
)
from razorpay_agent.gate import RulePolicyGateConfig


class TestSimulatorLegibility:
    def test_deterministic_given_seed(self):
        first = SimulatedBuyerModel(random.Random(11))
        second = SimulatedBuyerModel(random.Random(11))
        s1 = first.session(0)
        s2 = second.session(0)
        assert s1 == s2

    def test_discount_probability_rises_with_generosity_and_sensitivity(self):
        session = SimSession(0, "apparel", 2000.0, 10000.0, base_completion_prob=0.80)
        low = discount_completion_prob(session, 5.0)
        high = discount_completion_prob(session, 25.0)
        assert high > low
        apparel = discount_completion_prob(session, 10.0)
        electronics = discount_completion_prob(
            SimSession(1, "electronics", 2000.0, 10000.0, 0.80), 10.0
        )
        assert apparel > electronics

    def test_relevant_cheap_bundles_take_more_than_irrelevant_pricey_ones(self):
        session = SimSession(0, "apparel", 3000.0, 9000.0, 0.85)
        relevant_take, relevant_abandon = bundle_outcome_probs(session, 400.0, True)
        irrelevant_take, irrelevant_abandon = bundle_outcome_probs(session, 700.0, False)
        assert relevant_take > irrelevant_take
        assert relevant_abandon < irrelevant_abandon
        assert BUNDLE_IRRELEVANT_TAKE_RATE <= BUNDLE_RELEVANT_TAKE_RATE

    def test_irrelevant_bundle_can_have_negative_expected_value(self):
        session = SimSession(0, "apparel", 1200.0, 5000.0, 0.90)
        pricey_irrelevant = SimOffer("bundle", bundle_price_rupees=290.0, bundle_category_match=False)
        cheap_relevant = SimOffer("bundle", bundle_price_rupees=200.0, bundle_category_match=True)
        assert expected_net_revenue(session, pricey_irrelevant) < expected_net_revenue(
            session, cheap_relevant
        )

    def test_no_offer_expected_value_is_zero(self):
        session = SimSession(0, "apparel", 2000.0, 8000.0, 0.8)
        assert expected_net_revenue(session, None) == 0.0


class TestOfflineValidation:
    def test_run_produces_all_three_metrics(self, tmp_path):
        store = EvalStore(tmp_path / "eval.sqlite3")
        summary = run_offline_validation(store, seed=7, n_sessions=60)
        assert {"uplift_over_baseline", "gate_compliance_rate", "cumulative_regret"} <= set(summary)

    def test_metrics_live_in_valid_ranges(self, tmp_path):
        store = EvalStore(tmp_path / "eval.sqlite3")
        summary = run_offline_validation(store, seed=7, n_sessions=60)
        assert 0.0 <= summary["gate_compliance_rate"] <= 1.0
        assert summary["cumulative_regret"] >= 0.0

    def test_bandit_beats_fallback_on_controlled_conditions(self, tmp_path):
        store = EvalStore(tmp_path / "eval.sqlite3")
        summary = run_offline_validation(store, seed=7, n_sessions=400)
        assert summary["uplift_over_baseline"] > 0

    def test_report_reads_from_same_store_with_honesty_note(self, tmp_path):
        store = EvalStore(tmp_path / "eval.sqlite3")
        run_offline_validation(store, seed=3, n_sessions=50)
        report = latest_report(store)
        assert report is not None
        assert set(report["metrics"]) == {
            "uplift_over_baseline",
            "gate_compliance_rate",
            "cumulative_regret",
        }
        assert "not a prediction of real-world revenue" in report["honesty_note"]

    def test_empty_store_reports_nothing(self, tmp_path):
        store = EvalStore(tmp_path / "eval.sqlite3")
        assert latest_report(store) is None


class TestEvalEndpoint:
    def test_report_endpoint_serves_same_data_source(self, tmp_path):
        eval_store = EvalStore(tmp_path / "eval.sqlite3")
        run_offline_validation(eval_store, seed=5, n_sessions=40)

        repo = SessionRepository()
        pipeline = OfferPipeline(None, RulePolicyGateConfig("sku-socks", 499.0), EvalStore(":memory:"))
        app = build_app(DEMO_CATALOG, repo, pipeline, ScriptedPaymentProvider(), eval_store=eval_store)
        client = TestClient(app)

        response = client.get("/eval/report")
        assert response.status_code == 200
        body = response.json()
        assert body["metrics"]["gate_compliance_rate"] >= 0.0
        assert "not a prediction" in body["honesty_note"]

    def test_report_endpoint_without_eval_configured(self):
        repo = SessionRepository()
        pipeline = OfferPipeline(None, RulePolicyGateConfig("sku-socks", 499.0), EvalStore(":memory:"))
        app = build_app(DEMO_CATALOG, repo, pipeline, ScriptedPaymentProvider())
        response = TestClient(app).get("/eval/report")
        assert response.json() == {"status": "eval_not_configured"}


class TestCharts:
    def test_charts_render_to_files(self, tmp_path):
        from razorpay_agent.eval.charts import render_charts

        db = tmp_path / "eval.sqlite3"
        store = EvalStore(db)
        run_offline_validation(store, seed=9, n_sessions=40)
        written = render_charts(str(db), str(tmp_path / "out"))
        assert len(written) == 2
        for path in written:
            with open(path, "rb") as handle:
                assert handle.read(8) == b"\x89PNG\r\n\x1a\n"
