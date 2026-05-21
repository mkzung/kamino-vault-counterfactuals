"""kamino-vault-counterfactuals — historical replay + adverse-scenario stress
testing for Kamino Lend reserves and obligations on Solana.

Mirror of morpho-vault-counterfactuals (https://github.com/mkzung/morpho-vault-counterfactuals)
adapted for the Solana account model and Kamino's reserve/obligation structure.

Six detectors:
  - OracleStalenessReplay   (Pyth/Scope feed lag → bad debt frontier)
  - CollateralCascade       (price shock → liquidatable debt + liquidity gap)
  - DepositorExitShock      (top-N withdraw → post-exit utilization)
  - UtilizationBandBreach   (reserves above curator target)
  - LiquidationLatency      (CU-cost vs liquidator profit on Solana)
  - LTVDistributionStress   (fraction of debt near weighted threshold)
"""

from .detectors import (
    CollateralCascade,
    DepositorExitShock,
    DetectorResult,
    LiquidationLatency,
    LTVDistributionStress,
    OracleStalenessReplay,
    UtilizationBandBreach,
)
from .diff import MarketDiff, ReserveDiff, diff_snapshots, summarize_diff
from .fetch import (
    DEFAULT_RPC,
    KAMINO_MAIN_MARKET,
    fetch_market_snapshot,
    load_fixture,
    load_history,
)
from .html_report import as_html
from .report import as_json, as_markdown
from .runner import RunnerConfig, run_all_detectors, summarize
from .state import (
    KaminoMarketSnapshot,
    MarketHistory,
    ObligationBorrow,
    ObligationDeposit,
    ObligationState,
    ReserveState,
)

__version__ = "0.1.0"

__all__ = [
    # version
    "__version__",
    # state
    "KaminoMarketSnapshot",
    "MarketHistory",
    "ObligationBorrow",
    "ObligationDeposit",
    "ObligationState",
    "ReserveState",
    # detectors
    "CollateralCascade",
    "DepositorExitShock",
    "DetectorResult",
    "LiquidationLatency",
    "LTVDistributionStress",
    "OracleStalenessReplay",
    "UtilizationBandBreach",
    # runner
    "RunnerConfig",
    "run_all_detectors",
    "summarize",
    # diff
    "MarketDiff",
    "ReserveDiff",
    "diff_snapshots",
    "summarize_diff",
    # fetch
    "DEFAULT_RPC",
    "KAMINO_MAIN_MARKET",
    "fetch_market_snapshot",
    "load_fixture",
    "load_history",
    # reports
    "as_html",
    "as_json",
    "as_markdown",
]
