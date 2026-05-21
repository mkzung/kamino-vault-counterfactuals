# Changelog

## [0.1.0] — 2026-05-21

Initial release.

### Added

- Six counterfactual risk detectors for Kamino Lend markets on Solana:
  - `OracleStalenessReplay` — Pyth/Scope feed staleness → bad-debt frontier.
  - `CollateralCascade` — single-reserve price shock → liquidatable debt + liquidity gap.
  - `DepositorExitShock` — top-N depositor withdraw → post-exit utilization.
  - `UtilizationBandBreach` — reserves above curator target band.
  - `LiquidationLatency` — CU-cost vs liquidator profit at current Solana priority-fee.
  - `LTVDistributionStress` — fraction of debt near weighted liquidation threshold.

- `KaminoMarketSnapshot`, `ReserveState`, `ObligationState` domain models (Pydantic v2, frozen).

- Snapshot diffing (`diff_snapshots`, `MarketDiff`, `ReserveDiff`).

- Deterministic synthetic snapshot factory (`make_market_snapshot`) for tests + offline demos.

- Live RPC fetcher stub with documented anchorpy integration path.

- CLI: `kvcf demo | run | diff` with markdown / JSON / HTML outputs.

- Standalone HTML report renderer (Inca-style severity-coded design).

- 75 pytest tests, ruff + mypy CI on Py 3.10 / 3.11 / 3.12 on Ubuntu + macOS.

### Architecture

Direct port of [`morpho-vault-counterfactuals`](https://github.com/mkzung/morpho-vault-counterfactuals)
adapted for Solana's account model:

- Reserves are Kamino's analog to Morpho markets — config + liquidity in one account.
- Obligations hold multiple deposits AND borrows for one owner across reserves
  (unlike Morpho Blue's per-market positions).
- Prices stored in Pyth/Scope (value, exp) convention rather than Morpho's 36-decimal.
- Compute-budget cost model replaces Ethereum gas cost in `LiquidationLatency`.
