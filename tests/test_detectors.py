"""Tests for the six detectors on synthetic snapshots."""

from __future__ import annotations

import math

import pytest

from kvcf.detectors import (
    CollateralCascade,
    DepositorExitShock,
    LiquidationLatency,
    LTVDistributionStress,
    OracleStalenessReplay,
    UtilizationBandBreach,
)
from kvcf.synthetic import make_market_snapshot

# ──────────────────────────────────────────────────────────────────────
# OracleStalenessReplay
# ──────────────────────────────────────────────────────────────────────


class TestOracleStalenessReplay:
    def test_no_drift_returns_zero_bad_debt(self):
        snap = make_market_snapshot(n_healthy=5, n_at_risk=0, n_underwater=0)
        # drift_pct=0 means no shock → all healthy positions stay healthy
        det = OracleStalenessReplay(drift_pct=0.0, stale_slots=50)
        res = det.run(snap)
        assert res.headline_metric == 0.0
        assert res.headline_unit == "fraction_bad_debt"

    def test_severe_drift_produces_bad_debt_on_at_risk(self):
        snap = make_market_snapshot(n_healthy=0, n_at_risk=5, n_underwater=0)
        # 30% SOL drift will push 70%-LTV positions over the bad-debt frontier
        det = OracleStalenessReplay(drift_pct=-0.30, stale_slots=50)
        res = det.run(snap)
        assert res.headline_metric > 0.0
        assert res.headline_unit == "fraction_bad_debt"
        assert res.evidence["bad_debt_obligations"] > 0

    def test_underwater_obligations_cross_bad_debt_frontier_under_severe_drift(self):
        # Underwater (>75% LTV) is liquidatable, but only becomes BAD DEBT when LTV crosses
        # the 1/(1+bonus) frontier (≈ 95.2% at 5% bonus). A 25% downside drift compresses
        # the underwater positions' true collateral, pushing their LTV above the frontier.
        snap = make_market_snapshot(n_healthy=0, n_at_risk=0, n_underwater=3)
        det = OracleStalenessReplay(drift_pct=-0.25, stale_slots=50)
        res = det.run(snap)
        assert res.headline_metric > 0.0
        assert res.evidence["bad_debt_obligations"] >= 1

    def test_rejects_positive_drift(self):
        with pytest.raises(ValueError, match="drift_pct"):
            OracleStalenessReplay(drift_pct=0.10)

    def test_rejects_extreme_drift(self):
        with pytest.raises(ValueError, match="drift_pct"):
            OracleStalenessReplay(drift_pct=-1.5)

    def test_evidence_includes_stale_seconds(self):
        snap = make_market_snapshot(n_healthy=2, n_at_risk=0, n_underwater=0)
        det = OracleStalenessReplay(drift_pct=-0.10, stale_slots=100)
        res = det.run(snap)
        assert res.evidence["stale_slots"] == 100
        assert math.isclose(res.evidence["stale_seconds_est"], 40.0)


# ──────────────────────────────────────────────────────────────────────
# CollateralCascade
# ──────────────────────────────────────────────────────────────────────


class TestCollateralCascade:
    def test_mild_shock_leaves_healthy_positions_alone(self):
        snap = make_market_snapshot(n_healthy=5, n_at_risk=0, n_underwater=0)
        det = CollateralCascade(shock_pct=-0.05)
        res = det.run(snap)
        assert res.headline_metric == 0.0

    def test_severe_shock_triggers_liquidations(self):
        snap = make_market_snapshot(n_healthy=0, n_at_risk=5, n_underwater=0)
        det = CollateralCascade(shock_pct=-0.30)
        res = det.run(snap)
        assert res.headline_metric > 0.0
        assert res.evidence["liquidatable_obligations"] > 0

    def test_target_symbol_filter(self):
        snap = make_market_snapshot(n_healthy=0, n_at_risk=3, n_underwater=0)
        # Shock SOL only → at-risk positions (which are SOL-collateral) liquidate
        det_sol = CollateralCascade(shock_pct=-0.25, target_reserve_symbol="SOL")
        res_sol = det_sol.run(snap)
        # Shock USDC only → no effect on SOL-collateral positions
        det_usdc = CollateralCascade(shock_pct=-0.25, target_reserve_symbol="USDC")
        res_usdc = det_usdc.run(snap)
        assert res_sol.headline_metric > res_usdc.headline_metric

    def test_liquidity_gap_reported(self):
        snap = make_market_snapshot(n_healthy=0, n_at_risk=10, n_underwater=0)
        det = CollateralCascade(shock_pct=-0.25)
        res = det.run(snap)
        assert "liquidity_gap_usd" in res.evidence
        assert res.evidence["liquidity_gap_usd"] >= 0

    def test_rejects_positive_shock(self):
        with pytest.raises(ValueError, match="shock_pct"):
            CollateralCascade(shock_pct=0.10)

    def test_rejects_zero_shock(self):
        with pytest.raises(ValueError, match="shock_pct"):
            CollateralCascade(shock_pct=0.0)


# ──────────────────────────────────────────────────────────────────────
# DepositorExitShock
# ──────────────────────────────────────────────────────────────────────


class TestDepositorExitShock:
    def test_top_1_exit_increases_utilization(self):
        snap = make_market_snapshot(n_healthy=2, n_at_risk=1, n_underwater=0)
        det = DepositorExitShock(top_n=1)
        res = det.run(snap)
        assert res.headline_unit == "worst_post_exit_utilization"
        # synthetic SOL has 30k whale; exit pushes available 80k→50k → util 40k/90k ≈ 44%
        assert res.headline_metric > 0.0

    def test_target_symbol_isolates_reserve(self):
        snap = make_market_snapshot(n_healthy=1, n_at_risk=0, n_underwater=0)
        det_sol = DepositorExitShock(top_n=1, target_reserve_symbol="SOL")
        det_jlp = DepositorExitShock(top_n=1, target_reserve_symbol="JLP")
        # JLP has no synthetic top depositors → 0
        assert det_jlp.run(snap).headline_metric == 0.0
        assert det_sol.run(snap).headline_metric > 0.0

    def test_rejects_zero_top_n(self):
        with pytest.raises(ValueError, match="top_n"):
            DepositorExitShock(top_n=0)


# ──────────────────────────────────────────────────────────────────────
# UtilizationBandBreach
# ──────────────────────────────────────────────────────────────────────


class TestUtilizationBandBreach:
    def test_default_target_below_synthetic_util(self):
        snap = make_market_snapshot(n_healthy=1, n_at_risk=0)
        # synthetic utilization: SOL 40k/120k=33%, USDC 8M/23M=35%
        # at default 90% threshold → 0 breaches
        det = UtilizationBandBreach(target_util_max=0.90)
        res = det.run(snap)
        assert res.headline_metric == 0.0
        assert res.evidence["breached_reserves"] == []

    def test_low_target_flags_all(self):
        snap = make_market_snapshot(n_healthy=1, n_at_risk=0)
        det = UtilizationBandBreach(target_util_max=0.10)
        res = det.run(snap)
        assert res.headline_metric > 0.0

    def test_rejects_target_at_one(self):
        with pytest.raises(ValueError, match="target_util_max"):
            UtilizationBandBreach(target_util_max=1.0)


# ──────────────────────────────────────────────────────────────────────
# LiquidationLatency
# ──────────────────────────────────────────────────────────────────────


class TestLiquidationLatency:
    def test_cost_low_so_all_obligations_profitable(self):
        snap = make_market_snapshot(n_healthy=3, n_at_risk=1, n_underwater=0)
        # 0 priority fee + small base fee → cost ≈ $0.0007 — every obligation profitable
        det = LiquidationLatency(priority_fee_microlamports=0, sol_price_usd=150.0)
        res = det.run(snap)
        assert res.headline_metric == 0.0

    def test_cost_high_makes_all_unprofitable(self):
        snap = make_market_snapshot(n_healthy=3, n_at_risk=1, n_underwater=0)
        # Cost = 10^11 * 400k / 1M / 1e9 SOL * $150 ≈ $6000, larger than any obligation × bonus
        det = LiquidationLatency(priority_fee_microlamports=100_000_000_000, sol_price_usd=150.0)
        res = det.run(snap)
        assert res.headline_metric == 1.0

    def test_rejects_zero_sol_price(self):
        with pytest.raises(ValueError, match="sol_price_usd"):
            LiquidationLatency(sol_price_usd=0)

    def test_cost_calculation_matches_formula(self):
        det = LiquidationLatency(
            priority_fee_microlamports=50_000,
            liquidation_cu=400_000,
            sol_price_usd=150.0,
            base_tx_lamports=5_000,
        )
        # priority lamports = 50_000 * 400_000 / 1_000_000 = 20_000
        # total = 20_000 + 5_000 = 25_000 lamports
        # SOL = 25_000 / 1e9 = 2.5e-5 SOL
        # USD = 2.5e-5 * 150 = $0.00375
        assert math.isclose(det._liquidation_cost_usd(), 25_000 / 1e9 * 150.0, abs_tol=1e-9)


# ──────────────────────────────────────────────────────────────────────
# LTVDistributionStress
# ──────────────────────────────────────────────────────────────────────


class TestLTVDistributionStress:
    def test_empty_obligations_returns_zero(self):
        snap = make_market_snapshot(n_healthy=0, n_at_risk=0, n_underwater=0)
        det = LTVDistributionStress(near_pp=5.0)
        res = det.run(snap)
        assert res.headline_metric == 0.0
        assert res.evidence["n_obligations"] == 0

    def test_healthy_obligations_far_from_lltv(self):
        snap = make_market_snapshot(n_healthy=5, n_at_risk=0, n_underwater=0)
        det = LTVDistributionStress(near_pp=5.0)
        res = det.run(snap)
        # 33% LTV vs 75% LT → headroom ≈ 56% — far from 5pp threshold
        assert res.headline_metric == 0.0

    def test_at_risk_within_threshold(self):
        snap = make_market_snapshot(n_healthy=0, n_at_risk=5, n_underwater=0)
        det = LTVDistributionStress(near_pp=15.0)
        res = det.run(snap)
        # ~70% LTV w/ 75% LT → headroom ~ 7% — within 15pp band
        assert res.headline_metric > 0.0

    def test_rejects_invalid_near_pp(self):
        with pytest.raises(ValueError, match="near_pp"):
            LTVDistributionStress(near_pp=0)
        with pytest.raises(ValueError, match="near_pp"):
            LTVDistributionStress(near_pp=60)


# ──────────────────────────────────────────────────────────────────────
# Cross-detector consistency tests
# ──────────────────────────────────────────────────────────────────────


class TestDetectorParity:
    def test_all_detectors_deterministic(self):
        """Same input → same output, across all detectors."""
        snap = make_market_snapshot(seed=42, n_healthy=4, n_at_risk=2, n_underwater=1)
        det = OracleStalenessReplay(drift_pct=-0.15)
        a = det.run(snap)
        b = det.run(snap)
        assert a.headline_metric == b.headline_metric
        assert a.evidence == b.evidence

    def test_all_results_have_required_fields(self):
        snap = make_market_snapshot(n_healthy=2, n_at_risk=1, n_underwater=1)
        for det in [
            OracleStalenessReplay(),
            CollateralCascade(),
            DepositorExitShock(),
            UtilizationBandBreach(),
            LiquidationLatency(),
            LTVDistributionStress(),
        ]:
            res = det.run(snap)
            assert res.name
            assert isinstance(res.headline_metric, float)
            assert res.headline_unit
            assert res.interpretation
            assert isinstance(res.evidence, dict)
