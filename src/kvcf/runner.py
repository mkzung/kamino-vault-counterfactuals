"""Detector orchestration — runs all six detectors over one or more snapshots
and aggregates the DetectorResults for reporting.
"""

from __future__ import annotations

from dataclasses import dataclass

from .detectors import (
    CollateralCascade,
    DepositorExitShock,
    DetectorResult,
    LiquidationLatency,
    LTVDistributionStress,
    OracleStalenessReplay,
    UtilizationBandBreach,
)
from .state import KaminoMarketSnapshot


@dataclass(frozen=True)
class RunnerConfig:
    """Tunable parameters for each detector — defaults match Kamino Lend's
    mainstream Main-market profile (SOL/USDC/JLP collateral, 5-10% liq
    bonus, ~50-slot oracle staleness threshold).
    """

    # OracleStalenessReplay — stale_slots default 0 means "no staleness gate";
    # override to a positive value (e.g., 25 or 50) to only flag obligations
    # touching reserves whose oracle is currently lagging by that many slots.
    drift_pct: float = -0.10
    stale_slots: int = 0

    # CollateralCascade
    shock_pct: float = -0.20
    shock_target_symbol: str | None = None  # None = all collateral

    # DepositorExitShock
    exit_top_n: int = 1
    exit_target_symbol: str | None = None

    # UtilizationBandBreach
    util_target_max: float = 0.90

    # LiquidationLatency
    priority_fee_microlamports: int = 50_000
    liquidation_cu: int = 400_000
    sol_price_usd: float = 150.0

    # LTVDistributionStress
    near_pp: float = 5.0


def run_all_detectors(
    snapshot: KaminoMarketSnapshot, config: RunnerConfig | None = None
) -> list[DetectorResult]:
    """Run all six detectors against one snapshot. Returns results in a
    stable order: [oracle, cascade, exit, util, latency, distribution].
    """
    cfg = config or RunnerConfig()
    return [
        OracleStalenessReplay(
            drift_pct=cfg.drift_pct, stale_slots=cfg.stale_slots
        ).run(snapshot),
        CollateralCascade(
            shock_pct=cfg.shock_pct,
            target_reserve_symbol=cfg.shock_target_symbol,
        ).run(snapshot),
        DepositorExitShock(
            top_n=cfg.exit_top_n,
            target_reserve_symbol=cfg.exit_target_symbol,
        ).run(snapshot),
        UtilizationBandBreach(target_util_max=cfg.util_target_max).run(snapshot),
        LiquidationLatency(
            priority_fee_microlamports=cfg.priority_fee_microlamports,
            liquidation_cu=cfg.liquidation_cu,
            sol_price_usd=cfg.sol_price_usd,
        ).run(snapshot),
        LTVDistributionStress(near_pp=cfg.near_pp).run(snapshot),
    ]


def summarize(results: list[DetectorResult]) -> dict[str, float]:
    """Reduce to a {detector_name: headline_metric} dict for diffing."""
    return {r.name: r.headline_metric for r in results}
