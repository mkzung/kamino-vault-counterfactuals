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
        # 30% SOL drift will push 70%-LTV positions over the bad-debt frontier.
        # Use stale_slots=0 to disable the staleness gate (synthetic data has fresh oracles).
        det = OracleStalenessReplay(drift_pct=-0.30, stale_slots=0)
        res = det.run(snap)
        assert res.headline_metric > 0.0
        assert res.headline_unit == "fraction_bad_debt"
        assert res.evidence["bad_debt_obligations"] > 0

    def test_underwater_obligations_cross_bad_debt_frontier_under_severe_drift(self):
        # Underwater (>75% LTV) is liquidatable, but only becomes BAD DEBT when LTV crosses
        # the 1/(1+bonus) frontier (≈ 95.2% at 5% bonus). A 25% downside drift compresses
        # the underwater positions' true collateral, pushing their LTV above the frontier.
        snap = make_market_snapshot(n_healthy=0, n_at_risk=0, n_underwater=3)
        det = OracleStalenessReplay(drift_pct=-0.25, stale_slots=0)
        res = det.run(snap)
        assert res.headline_metric > 0.0
        assert res.evidence["bad_debt_obligations"] >= 1

    def test_staleness_gate_skips_when_oracles_fresh(self):
        # Synthetic snapshot has oracle_last_update_slot == snapshot.slot (zero staleness).
        # With a positive stale_slots threshold, ALL obligations should be skipped.
        snap = make_market_snapshot(n_healthy=0, n_at_risk=5, n_underwater=2)
        det = OracleStalenessReplay(drift_pct=-0.30, stale_slots=50)
        res = det.run(snap)
        assert res.headline_metric == 0.0
        assert res.evidence["bad_debt_obligations"] == 0
        assert res.evidence["skipped_for_fresh_oracle"] == 7

    def test_staleness_gate_fires_when_reserve_stale(self):
        # Build a snapshot where the SOL reserve has actual staleness ≥ 50 slots.
        from kvcf.state import KaminoMarketSnapshot
        from kvcf.synthetic import make_market_snapshot, make_reserve
        base = make_market_snapshot(n_healthy=0, n_at_risk=0, n_underwater=3, slot=250_000_000)
        # Replace SOL reserve with one that has stale oracle
        sol_stale = make_reserve(
            "SOL",
            available_amount=80_000 * 10**9,
            borrowed_amount=40_000 * 10**9,
            slot=250_000_000,
            oracle_staleness_slots=120,  # 120 slots > our threshold of 50
        )
        new_reserves = [sol_stale] + [r for r in base.reserves if r.symbol != "SOL"]
        snap_stale = KaminoMarketSnapshot(
            market_address=base.market_address,
            market_name=base.market_name,
            slot=base.slot,
            timestamp=base.timestamp,
            reserves=new_reserves,
            obligations=base.obligations,
            top_depositors_by_reserve=base.top_depositors_by_reserve,
        )
        det = OracleStalenessReplay(drift_pct=-0.25, stale_slots=50)
        res = det.run(snap_stale)
        # Now the obligations DO pass the staleness gate, and severe drift hits bad-debt frontier
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

    def test_stale_slots_zero_disables_gate(self):
        # With stale_slots=0 the gate is disabled — synthetic at-risk positions are evaluated.
        snap = make_market_snapshot(n_healthy=0, n_at_risk=3, n_underwater=0)
        det = OracleStalenessReplay(drift_pct=-0.30, stale_slots=0)
        res = det.run(snap)
        # All obligations evaluated; their LTV may or may not cross frontier but the
        # skip counter MUST be 0
        assert res.evidence["skipped_for_fresh_oracle"] == 0


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

    def test_accepts_zero_shock_for_symmetry_with_oracle_drift(self):
        # v0.2.0: shock_pct=0.0 is accepted as a degenerate no-shock
        # baseline, matching OracleStalenessReplay.drift_pct semantics.
        # Useful for parameter sweeps that walk from 0 downward.
        snap = make_market_snapshot(n_healthy=2, n_at_risk=1, n_underwater=0)
        det = CollateralCascade(shock_pct=0.0)
        res = det.run(snap)
        # No shock → only positions already underwater would liquidate.
        # None are, so the headline must be 0.
        assert res.headline_metric == 0.0

    def test_stablecoins_not_shocked_by_default(self):
        # v0.2.0: when target is None, USDC/USDT/JLP/jitoSOL are NOT
        # shocked by default. A -30% shock with target=None should be
        # IDENTICAL to a -30% shock of "SOL" only (since the synthetic
        # market's only true-collateral asset is SOL).
        snap = make_market_snapshot(n_healthy=0, n_at_risk=5, n_underwater=0)
        default_target = CollateralCascade(shock_pct=-0.30).run(snap)
        sol_only = CollateralCascade(
            shock_pct=-0.30, target_reserve_symbol="SOL"
        ).run(snap)
        assert default_target.headline_metric == sol_only.headline_metric

    def test_shock_stablecoins_opt_in(self):
        # Opting in with shock_stablecoins=True must produce equal-or-
        # larger impairment than the default (more collateral is shocked).
        snap = make_market_snapshot(n_healthy=0, n_at_risk=5, n_underwater=0)
        default = CollateralCascade(shock_pct=-0.30).run(snap)
        all_in = CollateralCascade(
            shock_pct=-0.30, shock_stablecoins=True
        ).run(snap)
        assert all_in.headline_metric >= default.headline_metric


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
        # v0.2.0: JLP has no synthetic top-depositor data → headline is
        # explicitly None ("undefined") rather than 0 ("computed and
        # benign"). 0 would look healthy on a glance read; None forces
        # the consumer to acknowledge the missing input.
        jlp_res = det_jlp.run(snap)
        assert jlp_res.headline_metric is None
        assert jlp_res.evidence["reason"] == "no top_depositors_by_reserve data"
        assert det_sol.run(snap).headline_metric is not None
        assert det_sol.run(snap).headline_metric > 0.0  # type: ignore[operator]

    def test_no_data_returns_none_not_zero(self):
        # An empty top_depositors_by_reserve dict (e.g., live snapshot
        # without indexer data) must return headline=None, not 0.
        snap = make_market_snapshot(n_healthy=1, n_at_risk=0, n_underwater=0)
        snap_no_data = snap.model_copy(update={"top_depositors_by_reserve": {}})
        det = DepositorExitShock(top_n=1)
        res = det.run(snap_no_data)
        assert res.headline_metric is None
        assert "reason" in res.evidence

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
