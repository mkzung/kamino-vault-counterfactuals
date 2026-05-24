# Changelog

## [0.2.0] — 2026-05-25 (Round-7 audit fixes — P0 + P1)

### Breaking

- **`KAMINO_MAIN_MARKET` constant removed from the public `kvcf` namespace.**
  The footgun default of `""` is gone; the symbol is now `Optional[str]` in
  `kvcf.fetch`, populated from the `KAMINO_MAIN_MARKET` environment variable
  if set. Pass `market_address=` explicitly to `fetch_market_snapshot()`.
  Callers that imported `from kvcf import KAMINO_MAIN_MARKET` must update to
  `from kvcf.fetch import KAMINO_MAIN_MARKET` (and accept that it may be None).
- **All Pydantic models now declare `extra='forbid'`.** Snapshots, reserves,
  obligations, and obligation deposit/borrow rows reject unknown fields at
  validation time. Existing valid fixtures continue to load; fixtures that
  carry extra keys (typos, stale schema, third-party annotations) will fail
  with a clear `ValidationError`.
- **`CollateralCascade` no longer shocks stablecoin / LP collateral by default.**
  When `target_reserve_symbol=None`, USDC / USDT / JLP / jitoSOL are skipped.
  Pass `shock_stablecoins=True` for the prior all-collateral behavior, or
  name the symbol explicitly via `target_reserve_symbol=`.
- **`DepositorExitShock` returns `headline_metric=None` when no top-depositor
  data is present.** Previously returned `0.0`, which looked healthy on a
  glance read. Consumers that compared the headline to a numeric threshold
  must now handle `None` (documented in `runner.summarize` return type).
  `DetectorResult.headline_metric` is now typed as `float | None`.
- **`CollateralCascade.__init__` accepts `shock_pct=0.0`.** Matches
  `OracleStalenessReplay.drift_pct=0.0` semantics — both are valid no-shock
  baselines for parameter sweeps. Callers that relied on `0.0` raising
  `ValueError` should validate upstream.
- **Dependency added: `base58>=2.1`** (used by `synthetic.py` to generate
  valid base58 mint and owner addresses).

### Added

- **`kvcf replay --fixtures-dir DIR [--out REPORT.json]` CLI command.**
  Iterates every `*.json` in the directory in slot-ascending order, runs all
  six detectors on each snapshot, and emits one chronological JSON report
  (`{n_snapshots, snapshots: [{fixture, slot, timestamp, market_address,
  results}, ...]}`). Makes good on the "historical replay" claim in the
  package description.
- **`as_html(now=...)` test seam and `SOURCE_DATE_EPOCH` support** —
  HTML reports are now deterministic when either the `now` argument is
  passed or the `SOURCE_DATE_EPOCH` env var is set (matches the
  reproducible-builds convention and lre-refusal-eval pattern).
- **`OracleStalenessReplay.bad_debt_obligations` no longer double-counts
  zero-collateral debt.** Degenerate obligations with debt but no
  attributable collateral reserve are excluded from the headline
  (they're already-bad-debt by definition and have no per-reserve home).
- **JSON-RPC error surfacing + retry on 429/5xx.** `fetch_market_snapshot`
  inspects `body["error"]` and raises with the full diagnostic
  (`code`, `message`, `data`). Network and rate-limit failures retry with
  1s / 2s / 4s exponential backoff before propagating.
- **`load_history` logs at WARNING when a fixture fails to load**
  (previously swallowed silently).
- **`test_all_bundled_fixtures_parse`** CI gate ensures every
  `data/fixtures/*.json` continues to load as a `KaminoMarketSnapshot`,
  protecting README examples from schema drift.
- **`test_fixtures_have_meaningful_diff`** asserts the bundled
  2026-05-21 / 2026-05-22 fixtures show a non-zero price delta and
  non-zero supply delta so the README diff example is not a no-op.
- Tagged the initial `v0.1.0` release retroactively from the first commit
  (`8cc6fec`) so the CHANGELOG entry resolves to a git ref.
- Test count: 80 → 95 (15 new tests: `extra=forbid` coverage on all 5
  models, `replay` CLI happy-path + bundled-fixtures sanity check,
  `as_html` determinism via `now` and `SOURCE_DATE_EPOCH`, base58
  round-trip, fixtures-have-meaningful-diff, depositor-exit None
  semantics, cascade stablecoin-skip behavior).

### Fixed

- **`OracleStalenessReplay` and `CollateralCascade` parameter validation
  is now symmetric** — both accept `0.0` for the shock parameter,
  documented as the no-shock baseline.
- **`report.as_markdown` and `html_report` no longer format booleans as
  numbers.** `isinstance(True, int)` is True in Python; the formatter now
  short-circuits on `bool` before the numeric branches.
- **CLI flag precedence unified.** Both `kvcf demo` and `kvcf run` now
  check `--json` before `--html` (JSON is the more compositable format).
- **Synthetic addresses are now valid base58.** Prior versions truncated
  sha256 hex (visually plausible, technically invalid since hex includes
  `0`, `O`, `I`, `l` characters that base58 omits). Generated addresses
  now round-trip through `base58.b58decode`.
- **`pyproject.toml [dev]` cleanup.** Removed unused `pandas` and
  `matplotlib` — neither is imported in `src/` or `tests/`.
- **mypy `python_version` bumped to 3.12** to match the newest version
  in the CI matrix (the floor remains 3.10 via `requires-python`).

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

- 80 pytest tests, ruff + mypy `--strict` CI on Py 3.10 / 3.11 / 3.12 on Ubuntu + macOS.

### Architecture

Direct port of [`morpho-vault-counterfactuals`](https://github.com/mkzung/morpho-vault-counterfactuals)
adapted for Solana's account model:

- Reserves are Kamino's analog to Morpho markets — config + liquidity in one account.
- Obligations hold multiple deposits AND borrows for one owner across reserves
  (unlike Morpho Blue's per-market positions).
- Prices stored in Pyth/Scope (value, exp) convention rather than Morpho's 36-decimal.
- Compute-budget cost model replaces Ethereum gas cost in `LiquidationLatency`.
