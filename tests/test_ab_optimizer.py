"""
Tests for A/B Auto-Optimizer (Module 7).

Coverage:
- Significance calculation (Welch's t-test)
- Min sample enforcement
- Auto-promotion on significance
- Manual override
- Concurrent experiment isolation
- Cancellation with cleanup
- API router endpoints (create/active/report/results/promote/cancel)
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.intelligence.ab_optimizer", reason="module not available in current build")
import threading
from pathlib import Path

import pytest
from tokenpak.intelligence.ab_optimizer import (
    MIN_SAMPLES,
    ABOptimizerStore,
    ExperimentStatus,
    PromotionAction,
    VariantStats,
    _welch_t_pvalue,
    compute_significance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_store(tmp_path: Path) -> ABOptimizerStore:
    """Fresh store backed by a temp SQLite file."""
    return ABOptimizerStore(db_path=tmp_path / "ab_test.db")


# ---------------------------------------------------------------------------
# 1. Statistical significance — _welch_t_pvalue
# ---------------------------------------------------------------------------

class TestWelchTPValue:
    def test_identical_distributions_returns_1(self):
        """Identical means → not significant."""
        p = _welch_t_pvalue(0.5, 0.01, 100, 0.5, 0.01, 100)
        assert p > 0.05

    def test_very_different_means_small_pvalue(self):
        """Large effect size → very small p-value."""
        p = _welch_t_pvalue(0.8, 0.001, 200, 0.2, 0.001, 200)
        assert p < 0.001

    def test_small_sample_returns_1(self):
        """n < 2 → returns 1.0 (cannot test)."""
        p = _welch_t_pvalue(0.8, 0.01, 1, 0.2, 0.01, 1)
        assert p == 1.0

    def test_zero_variance_equal_means(self):
        """Zero variance, equal means → p = 1.0 (no difference)."""
        p = _welch_t_pvalue(0.5, 0.0, 100, 0.5, 0.0, 100)
        assert p == 1.0

    def test_moderate_difference_moderate_pvalue(self):
        """Moderate effect → p between 0 and 1."""
        p = _welch_t_pvalue(0.55, 0.05, 50, 0.50, 0.05, 50)
        assert 0.0 < p < 1.0


# ---------------------------------------------------------------------------
# 2. compute_significance
# ---------------------------------------------------------------------------

class TestComputeSignificance:
    def _make_stats(self, name: str, savings: float, quality: float, latency: float, n: int) -> VariantStats:
        """Helper: create VariantStats with consistent data."""
        s = VariantStats(name=name)
        s.samples = n
        s.token_savings_sum = savings * n
        s.token_savings_sq_sum = (savings ** 2) * n + 0.0001 * n  # small variance
        s.quality_sum = quality * n
        s.quality_sq_sum = (quality ** 2) * n + 0.0001 * n
        s.latency_sum = latency * n
        s.latency_sq_sum = (latency ** 2) * n + 1.0 * n  # small variance
        return s

    def test_insufficient_samples_not_significant(self):
        """Below min_samples → not significant regardless of effect."""
        ctrl = self._make_stats("control", 0.3, 0.8, 100.0, MIN_SAMPLES - 1)
        trt = self._make_stats("treatment", 0.9, 0.95, 50.0, MIN_SAMPLES - 1)
        result = compute_significance(ctrl, trt)
        assert not result.significant

    def test_large_effect_above_min_samples_significant(self):
        """Large effect + enough samples → significant with winner."""
        ctrl = self._make_stats("control", 0.2, 0.6, 200.0, MIN_SAMPLES * 3)
        trt = self._make_stats("treatment", 0.8, 0.95, 80.0, MIN_SAMPLES * 3)
        result = compute_significance(ctrl, trt)
        assert result.significant
        assert result.winner == "treatment"
        assert result.winner_metric is not None

    def test_no_difference_no_winner(self):
        """Identical stats → not significant."""
        ctrl = self._make_stats("control", 0.5, 0.8, 100.0, MIN_SAMPLES * 2)
        trt = self._make_stats("treatment", 0.5, 0.8, 100.0, MIN_SAMPLES * 2)
        result = compute_significance(ctrl, trt)
        assert not result.significant
        assert result.winner is None

    def test_control_wins_when_better(self):
        """Control with better metrics wins."""
        ctrl = self._make_stats("control", 0.9, 0.95, 50.0, MIN_SAMPLES * 2)
        trt = self._make_stats("treatment", 0.2, 0.5, 300.0, MIN_SAMPLES * 2)
        result = compute_significance(ctrl, trt)
        assert result.significant
        assert result.winner == "control"

    def test_result_has_all_p_values(self):
        """Result dict contains all three p-values."""
        ctrl = self._make_stats("control", 0.5, 0.8, 100.0, MIN_SAMPLES)
        trt = self._make_stats("treatment", 0.5, 0.8, 100.0, MIN_SAMPLES)
        result = compute_significance(ctrl, trt)
        d = result.to_dict()
        assert "p_values" in d
        assert "token_savings" in d["p_values"]
        assert "quality" in d["p_values"]
        assert "latency" in d["p_values"]


# ---------------------------------------------------------------------------
# 3. ABOptimizerStore — CRUD
# ---------------------------------------------------------------------------

class TestABOptimizerStore:
    def test_create_experiment(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment(
            name="Test Exp",
            description="Testing",
            control_name="v1",
            treatment_name="v2",
            tags=["test"],
        )
        assert exp.id
        assert exp.name == "Test Exp"
        assert exp.control_name == "v1"
        assert exp.treatment_name == "v2"
        assert exp.status == ExperimentStatus.ACTIVE
        assert exp.tags == ["test"]

    def test_get_experiment(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("Exp A")
        fetched = tmp_store.get_experiment(exp.id)
        assert fetched is not None
        assert fetched.id == exp.id
        assert fetched.name == "Exp A"

    def test_get_nonexistent_returns_none(self, tmp_store: ABOptimizerStore):
        assert tmp_store.get_experiment("nonexistent-id") is None

    def test_list_experiments(self, tmp_store: ABOptimizerStore):
        tmp_store.create_experiment("A")
        tmp_store.create_experiment("B")
        all_exps = tmp_store.list_experiments()
        assert len(all_exps) == 2

    def test_list_filter_active(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("A")
        tmp_store.cancel_experiment(exp.id)
        tmp_store.create_experiment("B")
        active = tmp_store.list_experiments(status_filter="active")
        assert len(active) == 1
        assert active[0].name == "B"

    def test_cancel_experiment(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("Cancel Me")
        cancelled = tmp_store.cancel_experiment(exp.id)
        assert cancelled.status == ExperimentStatus.CANCELLED
        assert cancelled.completed_at is not None

    def test_cancel_nonexistent_raises(self, tmp_store: ABOptimizerStore):
        with pytest.raises(ValueError, match="not found"):
            tmp_store.cancel_experiment("no-such-id")


# ---------------------------------------------------------------------------
# 4. Record observations + auto-promotion
# ---------------------------------------------------------------------------

class TestRecordObservations:
    def _fill_variant(
        self,
        store: ABOptimizerStore,
        exp_id: str,
        variant: str,
        n: int,
        savings: float,
        quality: float,
        latency: float,
    ) -> None:
        for _ in range(n):
            store.record_observation(
                exp_id=exp_id,
                variant=variant,
                token_savings=savings + 0.001,  # tiny jitter for variance
                quality_score=quality,
                latency_ms=latency,
            )

    def test_below_min_samples_no_result(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("MinSample")
        # Only 10 observations — below MIN_SAMPLES
        for i in range(10):
            result = tmp_store.record_observation(
                exp.id, "control", 0.5, 0.8, 100.0
            )
        assert result is None

    def test_auto_promotion_on_clear_winner(self, tmp_store: ABOptimizerStore):
        """Massive effect size should auto-promote after enough samples."""
        exp = tmp_store.create_experiment("AutoPromote")
        # Fill both variants with very different metrics
        self._fill_variant(tmp_store, exp.id, "control", MIN_SAMPLES, 0.1, 0.4, 500.0)
        self._fill_variant(tmp_store, exp.id, "treatment", MIN_SAMPLES, 0.9, 0.95, 50.0)

        # After filling, check the experiment status
        final = tmp_store.get_experiment(exp.id)
        assert final.status == ExperimentStatus.COMPLETED
        assert final.winner == "treatment"
        assert final.promotion_action == PromotionAction.AUTO

    def test_no_auto_promotion_without_significance(self, tmp_store: ABOptimizerStore):
        """Identical metrics → experiment stays active."""
        exp = tmp_store.create_experiment("NoPromo")
        self._fill_variant(tmp_store, exp.id, "control", MIN_SAMPLES, 0.5, 0.8, 100.0)
        self._fill_variant(tmp_store, exp.id, "treatment", MIN_SAMPLES, 0.5, 0.8, 100.0)
        final = tmp_store.get_experiment(exp.id)
        assert final.status == ExperimentStatus.ACTIVE
        assert final.winner is None

    def test_record_on_inactive_experiment_raises(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("Inactive")
        tmp_store.cancel_experiment(exp.id)
        with pytest.raises(ValueError, match="not active"):
            tmp_store.record_observation(exp.id, "control", 0.5, 0.8, 100.0)

    def test_record_unknown_variant_raises(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("BadVariant")
        with pytest.raises(ValueError, match="Unknown variant"):
            tmp_store.record_observation(exp.id, "ghost_variant", 0.5, 0.8, 100.0)


# ---------------------------------------------------------------------------
# 5. Manual override
# ---------------------------------------------------------------------------

class TestManualOverride:
    def test_force_winner_control(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("ManualWin", control_name="ctrl", treatment_name="trt")
        result = tmp_store.force_winner(exp.id, "ctrl")
        assert result.winner == "ctrl"
        assert result.promotion_action == PromotionAction.MANUAL
        assert result.manual_override == "ctrl"
        assert result.status == ExperimentStatus.COMPLETED

    def test_force_winner_treatment(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("ManualWin2")
        result = tmp_store.force_winner(exp.id, "treatment")
        assert result.winner == "treatment"
        assert result.promotion_action == PromotionAction.MANUAL

    def test_force_unknown_variant_raises(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("ManualBad")
        with pytest.raises(ValueError, match="Unknown variant"):
            tmp_store.force_winner(exp.id, "phantom")

    def test_force_nonexistent_experiment_raises(self, tmp_store: ABOptimizerStore):
        with pytest.raises(ValueError, match="not found"):
            tmp_store.force_winner("no-such-id", "control")


# ---------------------------------------------------------------------------
# 6. Concurrent experiment isolation
# ---------------------------------------------------------------------------

class TestConcurrentExperiments:
    def test_two_concurrent_experiments_independent(self, tmp_store: ABOptimizerStore):
        """Two experiments don't interfere with each other."""
        exp_a = tmp_store.create_experiment("ConcurrentA")
        exp_b = tmp_store.create_experiment("ConcurrentB")

        errors = []

        def record_a():
            try:
                for _ in range(20):
                    tmp_store.record_observation(exp_a.id, "control", 0.3, 0.7, 200.0)
            except Exception as e:
                errors.append(e)

        def record_b():
            try:
                for _ in range(20):
                    tmp_store.record_observation(exp_b.id, "treatment", 0.6, 0.9, 100.0)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=record_a)
        t2 = threading.Thread(target=record_b)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, f"Thread errors: {errors}"
        a_final = tmp_store.get_experiment(exp_a.id)
        b_final = tmp_store.get_experiment(exp_b.id)
        assert a_final.control_stats.samples == 20
        assert b_final.treatment_stats.samples == 20

    def test_concurrent_writes_same_experiment(self, tmp_store: ABOptimizerStore):
        """Concurrent writes to same experiment remain consistent."""
        exp = tmp_store.create_experiment("ConcurrentSame")
        errors = []

        def write_control():
            try:
                for _ in range(30):
                    tmp_store.record_observation(exp.id, "control", 0.5, 0.8, 100.0)
            except Exception as e:
                errors.append(e)

        def write_treatment():
            try:
                for _ in range(30):
                    tmp_store.record_observation(exp.id, "treatment", 0.5, 0.8, 100.0)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=write_control)
        t2 = threading.Thread(target=write_treatment)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors
        final = tmp_store.get_experiment(exp.id)
        assert final.control_stats.samples == 30
        assert final.treatment_stats.samples == 30


# ---------------------------------------------------------------------------
# 7. get_results
# ---------------------------------------------------------------------------

class TestGetResults:
    def test_get_results_structure(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("ResultsTest")
        results = tmp_store.get_results(exp.id)
        assert "significance" in results
        assert "min_samples_met" in results
        assert results["min_samples_required"] == MIN_SAMPLES
        assert results["confidence_level"] == 0.95

    def test_get_results_nonexistent_raises(self, tmp_store: ABOptimizerStore):
        with pytest.raises(ValueError, match="not found"):
            tmp_store.get_results("no-such-id")

    def test_min_samples_met_flag(self, tmp_store: ABOptimizerStore):
        exp = tmp_store.create_experiment("MinCheck")
        results = tmp_store.get_results(exp.id)
        assert results["min_samples_met"] is False

        # Add enough samples
        for _ in range(MIN_SAMPLES):
            tmp_store.record_observation(exp.id, "control", 0.5, 0.8, 100.0)
            tmp_store.record_observation(exp.id, "treatment", 0.5, 0.8, 100.0)
        results = tmp_store.get_results(exp.id)
        assert results["min_samples_met"] is True


# ---------------------------------------------------------------------------
# 8. Router endpoint tests (using TestClient)
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from tokenpak.intelligence.ab_router import ab_router
    from tokenpak.intelligence.auth import LicenseTier
    _HAS_FASTAPI_CLIENT = True
except ImportError:
    _HAS_FASTAPI_CLIENT = False


@pytest.mark.skipif(not _HAS_FASTAPI_CLIENT, reason="FastAPI TestClient not available")
class TestABRouterEndpoints:
    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        """Build a minimal FastAPI app with the AB router and Pro auth."""
        import tokenpak.intelligence.ab_router as ab_mod

        # Point router at temp DB
        db_path = tmp_path / "router_test.db"
        # Reset singleton
        if hasattr(ab_mod.ab_router, "_store_instance"):
            del ab_mod.ab_router._store_instance

        store = ABOptimizerStore(db_path=db_path)
        ab_mod.ab_router._store_instance = store  # type: ignore[attr-defined]

        app = FastAPI()
        app.include_router(ab_router, prefix="/v1")

        # Middleware to inject Pro tier
        @app.middleware("http")
        async def inject_tier(request, call_next):
            request.state.tier = LicenseTier.PRO
            return await call_next(request)

        return TestClient(app)

    def test_create_experiment_201(self, client: TestClient):
        resp = client.post("/v1/ab/experiments", json={
            "name": "My Exp",
            "control_name": "v1",
            "treatment_name": "v2",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Exp"
        assert data["status"] == "active"

    def test_list_experiments(self, client: TestClient):
        client.post("/v1/ab/experiments", json={"name": "E1"})
        client.post("/v1/ab/experiments", json={"name": "E2"})
        resp = client.get("/v1/ab/experiments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] >= 2

    def test_list_filter_active(self, client: TestClient):
        resp = client.get("/v1/ab/experiments?filter=active")
        assert resp.status_code == 200

    def test_list_invalid_filter(self, client: TestClient):
        resp = client.get("/v1/ab/experiments?filter=garbage")
        assert resp.status_code == 400

    def test_get_experiment_404(self, client: TestClient):
        resp = client.get("/v1/ab/experiments/no-such-id")
        assert resp.status_code == 404

    def test_report_observation(self, client: TestClient):
        # Create
        exp_id = client.post("/v1/ab/experiments", json={"name": "ReportTest"}).json()["id"]
        # Report
        resp = client.post(f"/v1/ab/experiments/{exp_id}/report", json={
            "variant": "control",
            "token_savings": 0.35,
            "quality_score": 0.88,
            "latency_ms": 120.5,
        })
        assert resp.status_code == 200
        assert resp.json()["recorded"] is True

    def test_report_unknown_variant_400(self, client: TestClient):
        exp_id = client.post("/v1/ab/experiments", json={"name": "BadVariant"}).json()["id"]
        resp = client.post(f"/v1/ab/experiments/{exp_id}/report", json={
            "variant": "phantom",
            "token_savings": 0.5,
            "quality_score": 0.8,
            "latency_ms": 100.0,
        })
        assert resp.status_code == 400

    def test_get_results(self, client: TestClient):
        exp_id = client.post("/v1/ab/experiments", json={"name": "ResultsAPI"}).json()["id"]
        resp = client.get(f"/v1/ab/experiments/{exp_id}/results")
        assert resp.status_code == 200
        data = resp.json()
        assert "significance" in data
        assert "min_samples_met" in data

    def test_promote_winner(self, client: TestClient):
        exp_id = client.post("/v1/ab/experiments", json={"name": "Promote"}).json()["id"]
        resp = client.post(f"/v1/ab/experiments/{exp_id}/promote", json={
            "variant": "control",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["promoted"] is True
        assert data["winner"] == "control"

    def test_cancel_experiment(self, client: TestClient):
        exp_id = client.post("/v1/ab/experiments", json={"name": "CancelAPI"}).json()["id"]
        resp = client.post(f"/v1/ab/experiments/{exp_id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cancelled"] is True
        assert data["experiment"]["status"] == "cancelled"

    def test_pro_guard_free_tier_rejected(self, tmp_path: Path):
        """Free tier cannot access A/B endpoints."""
        import tokenpak.intelligence.ab_router as ab_mod
        if hasattr(ab_mod.ab_router, "_store_instance"):
            del ab_mod.ab_router._store_instance

        app = FastAPI()
        app.include_router(ab_router, prefix="/v1")

        @app.middleware("http")
        async def inject_free(request, call_next):
            request.state.tier = LicenseTier.FREE
            return await call_next(request)

        free_client = TestClient(app)
        resp = free_client.get("/v1/ab/experiments")
        assert resp.status_code == 403
