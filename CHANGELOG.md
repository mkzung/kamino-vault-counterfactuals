# Changelog

## [0.1.1] — 2026-05-21 (post-audit corrections)

### Fixed

- **Hardcoded base58 addresses replaced with clearly-synthetic placeholders.**
  Prior `0.1.0` shipped reserve/market addresses that LOOKED like real Kamino
  mainnet PDAs but were not verified against any source. Synthetic fixtures
  now use prefix-tagged placeholders (e.g. `SyntheticSoLReserve…`) that cannot
  be confused with mainnet state.
- **`KAMINO_MAIN_MARKET` constant in `fetch.py` no longer defaults to a
  pretend mainnet address.** Callers must pass the market PDA explicitly,
  forcing them to look up the canonical value from Kamino docs.
- **`OracleStalenessReplay.stale_slots` now actually gates the math.** Prior
  versions accepted the parameter but only echoed it in the interpretation
  string. The detector now filters obligations to those whose collateral
  reserves are *currently* lagging by ≥ `stale_slots`. `stale_slots=0`
  disables the gate (matches the prior behavior).
- README/state.py: dropped the "Steakhouse / MEV Capital / B.Protocol /
  Block Analitica" curator name-drop — those are Morpho (Ethereum) curators,
  not Kamino. Replaced with truthful audience: K-Lend strategy authors,
  Kamino risk team, external Solana DeFi risk analysts.
- TVL claim softened from "the largest lending protocol on Solana
  (~\$4-5B TVL as of May 2026)" → "one of the largest lending protocols on
  Solana by TVL" — the prior phrasing implied a precise market ranking that
  could not be sourced.
- Added "Note on data" callout in README making the synthetic nature of
  shipped fixtures explicit.

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
