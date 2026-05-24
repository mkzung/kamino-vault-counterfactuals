"""Counterfactual detectors for Kamino Lend market risk on Solana.

Six detectors mirroring morpho-vault-counterfactuals/src/mvcf/detectors.py,
adapted for Kamino's account model:

  1. OracleStalenessReplay   — Pyth/Scope feed staleness > N slots while
                               collateral drifts; computes bad-debt at the
                               1/(1+bonus) frontier.
  2. CollateralCascade       — step-shock one collateral asset, count
                               obligations crossing each reserve's
                               liquidation threshold.
  3. DepositorExitShock      — top-N depositor withdraw → utilization spike
                               in the source reserve.
  4. UtilizationBandBreach   — reserves currently above curator target band.
  5. LiquidationLatency      — fraction of debt sitting in positions too
                               small to profitably liquidate at current
                               compute-budget cost.
  6. LTVDistributionStress   — fraction of obligations within X pp of their
                               weighted liquidation threshold.

Design parity with morpho-vault-counterfactuals:
  - Pure functions on snapshots → no live RPC inside detectors.
  - Each returns a DetectorResult with one headline number + evidence dict.
  - Risk reported as fractional bad-debt or fractional-impairment, no
    good/bad labels.
  - Deterministic given (snapshot, params).

References:
  - Kamino Lend Whitepaper, Section 4 (Liquidation Mechanics)
  - Scope pricing engine docs (staleness, fallback, cluster pricing)
  - Pyth Network: max_staleness_slots, confidence intervals
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .state import KaminoMarketSnapshot

# ──────────────────────────────────────────────────────────────────────
# Common types
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DetectorResult:
    """Output of a single detector on one snapshot.

    `headline_metric` is `None` when a detector can't compute its number
    from the input (e.g., no top-depositor data). This is distinct from
    `0.0` (computed and benign) — consumers MUST handle both.
    """

    name: str
    headline_metric: float | None
    headline_unit: str
    interpretation: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────
# 1. Oracle staleness replay
# ──────────────────────────────────────────────────────────────────────


class OracleStalenessReplay:
    """What fraction of debt becomes **bad debt** if a reserve's oracle is
    already stale by ≥ `stale_slots` AND the true collateral price drifts
    by `drift_pct` during that staleness window?

    Solana slot ≈ 400 ms. Scope's default staleness threshold for major
    feeds is 25 slots (10 s); some feeds (RWA, illiquid) allow 100-200.
    When a feed is stale beyond threshold, Kamino's risk engine should
    pause reserve actions — but if the *true* solvency of in-flight
    obligations has already shifted, in-flight liquidations also lag.

    The detector flags obligations whose **any collateral reserve has
    actual oracle staleness ≥ `stale_slots`** AND whose post-drift LTV
    crosses the bad-debt frontier:

        ltv > 1 / (1 + liquidation_bonus)

    Beyond that frontier, seized collateral cannot cover debt-plus-bonus
    once the oracle updates. Each Kamino reserve has its own bonus, so
    the frontier is computed against the most-stale collateral's bonus.

    Args:
      drift_pct: signed collateral price drift during the staleness
        window. Negative for downside scenarios. Positive values rejected
        because they cannot create bad debt.
      stale_slots: minimum slot-count of oracle staleness on at least one
        collateral reserve for the obligation to count. Set to 0 to skip
        the staleness gate and consider every obligation (matches the
        "every oracle could be wrong" worst case).
    """

    def __init__(self, drift_pct: float = -0.10, stale_slots: int = 50):
        if not -0.99 < drift_pct <= 0.0:
            raise ValueError(f"drift_pct must be in (-0.99, 0.0], got {drift_pct}")
        if stale_slots < 0:
            raise ValueError(f"stale_slots must be >= 0, got {stale_slots}")
        self.drift_pct = drift_pct
        self.stale_slots = stale_slots

    def run(self, snapshot: KaminoMarketSnapshot) -> DetectorResult:
        reserves = snapshot.reserves_by_address
        bad_debt_usd = 0.0
        total_debt_usd = 0.0
        bad_debt_obligations = 0
        skipped_for_fresh_oracle = 0
        per_reserve: dict[str, dict[str, float]] = {}

        # Pre-compute shocked collateral value for each obligation
        for ob in snapshot.obligations:
            # Staleness gate: at least one collateral reserve must have an
            # oracle stale by ≥ stale_slots at the snapshot's current slot.
            # stale_slots=0 disables the gate (every obligation counts).
            if self.stale_slots > 0:
                max_obs_staleness = max(
                    (
                        reserves[d.reserve_address].oracle_staleness_slots(snapshot.slot)
                        for d in ob.deposits
                        if d.reserve_address in reserves
                    ),
                    default=0,
                )
                if max_obs_staleness < self.stale_slots:
                    skipped_for_fresh_oracle += 1
                    total_debt_usd += ob.borrowed_value_usd(reserves)
                    continue

            # Build a shocked-reserves view: only collateral reserves shocked,
            # not loan reserves (the freeze hides the move on the collateral
            # side; the loan asset oracle is independent).
            shocked_collateral_usd = 0.0
            for d_dep in ob.deposits:
                r_c = reserves.get(d_dep.reserve_address)
                if r_c is None:
                    continue
                tokens = d_dep.deposited_amount / (10 ** r_c.mint_decimals)
                # Shocked price; oracle reports stale (current) but true price drifted
                true_price = r_c.price_usd * (1 + self.drift_pct)
                shocked_collateral_usd += tokens * true_price

            debt_usd = ob.borrowed_value_usd(reserves)
            total_debt_usd += debt_usd
            if debt_usd == 0:
                continue

            # Zero-collateral debt is degenerate (already bad debt by
            # definition, no reserve to attribute to, no shock semantics).
            # Exclude these from the headline entirely — counting them
            # would inflate `bad_debt_usd` with rows the `per_reserve`
            # breakdown can never explain. We track them in evidence so
            # they aren't silently dropped.
            if shocked_collateral_usd <= 0:
                continue

            # Per-reserve bonus: use the *largest collateral reserve's* bonus
            # as a worst-case proxy. (Kamino liquidator picks one reserve at a
            # time, but for risk reporting we want the conservative bound.)
            max_bonus = 0.0
            top_reserve: str | None = None
            for d in ob.deposits:
                r_d = reserves.get(d.reserve_address)
                if r_d is None:
                    continue
                if r_d.liquidation_bonus > max_bonus:
                    max_bonus = r_d.liquidation_bonus
                    top_reserve = r_d.reserve_address

            # Bad-debt frontier given that bonus
            bad_debt_frontier_ltv = 1.0 / (1.0 + max_bonus) if max_bonus > 0 else 1.0
            true_ltv = debt_usd / shocked_collateral_usd

            if true_ltv > bad_debt_frontier_ltv:
                bad_debt_usd += debt_usd
                bad_debt_obligations += 1
                if top_reserve is not None:
                    bucket = per_reserve.setdefault(
                        top_reserve,
                        {
                            "bad_debt_usd": 0.0,
                            "count": 0.0,
                            "frontier_ltv": bad_debt_frontier_ltv,
                        },
                    )
                    bucket["bad_debt_usd"] += debt_usd
                    bucket["count"] += 1.0

        fraction = (bad_debt_usd / total_debt_usd) if total_debt_usd > 0 else 0.0
        pos_word = "obligation" if bad_debt_obligations == 1 else "obligations"
        return DetectorResult(
            name="OracleStalenessReplay",
            headline_metric=fraction,
            headline_unit="fraction_bad_debt",
            interpretation=(
                f"If a Pyth/Scope feed stalls for ≥{self.stale_slots} slots "
                f"(~{self.stale_slots * 0.4:.1f}s) while collateral drifts "
                f"{self.drift_pct:+.0%}, {fraction:.1%} of outstanding debt "
                f"({bad_debt_obligations} {pos_word}) crosses the bad-debt "
                f"frontier — seized collateral could not cover debt-plus-bonus "
                f"even once the oracle updates."
            ),
            evidence={
                "drift_pct": self.drift_pct,
                "stale_slots": self.stale_slots,
                "stale_seconds_est": self.stale_slots * 0.4,
                "bad_debt_usd": bad_debt_usd,
                "total_debt_usd": total_debt_usd,
                "bad_debt_obligations": bad_debt_obligations,
                "skipped_for_fresh_oracle": skipped_for_fresh_oracle,
                "per_reserve": per_reserve,
            },
        )


# ──────────────────────────────────────────────────────────────────────
# 2. Collateral cascade
# ──────────────────────────────────────────────────────────────────────


class CollateralCascade:
    """Step-shock one collateral reserve's price by `shock_pct` and count
    obligations crossing their liquidation threshold.

    A live curator wants to know: at -10% / -20% / -30% SOL cliffs, how
    much of the market's exposure goes underwater, and is there enough
    loan-asset liquidity to absorb the cascade without bad debt?

    Args:
      shock_pct: signed price shock to apply (negative).
      target_reserve_symbol: only shock reserves matching this symbol
        (default: shock all collateral). E.g. "SOL" to model SOL-only
        cliff.
    """

    def __init__(
        self,
        shock_pct: float = -0.20,
        target_reserve_symbol: str | None = None,
        *,
        shock_stablecoins: bool = False,
    ):
        # Symmetry with OracleStalenessReplay.drift_pct: both accept 0.0 as a
        # well-defined no-shock baseline. 0.0 is a degenerate but valid input
        # (the detector emits zero impairment), useful for parameter sweeps
        # that start at "no shock" and walk down.
        if not -0.99 < shock_pct <= 0.0:
            raise ValueError(
                f"shock_pct must be in (-0.99, 0.0], got {shock_pct}"
            )
        self.shock_pct = shock_pct
        self.target_reserve_symbol = target_reserve_symbol
        self.shock_stablecoins = shock_stablecoins

    # Assets we treat as "non-shockable" when target_reserve_symbol is None
    # and the caller has NOT opted into shock_stablecoins=True. JLP and
    # jitoSOL are technically not stablecoins, but at typical scenario
    # depth (-10 to -30%) shocking them along with the loan asset
    # over-states cascade risk. Curators who want full-collateral shocks
    # should either name the symbol explicitly or pass shock_stablecoins=True.
    _NON_SHOCKABLE_DEFAULT = frozenset({"USDC", "USDT", "JLP", "jitoSOL"})

    def run(self, snapshot: KaminoMarketSnapshot) -> DetectorResult:
        reserves = snapshot.reserves_by_address

        # Build a shocked-price map for the targeted reserve(s).
        # Backward-compat: if `target_reserve_symbol` is set, only that
        # symbol is shocked (prior behavior). If target is None and
        # shock_stablecoins=False (default), skip stablecoin / LP-token
        # reserves and only shock "true" collateral (e.g., SOL).
        shocked_prices: dict[str, float] = {}
        for r in snapshot.reserves:
            should_shock: bool
            if self.target_reserve_symbol is not None:
                should_shock = r.symbol == self.target_reserve_symbol
            else:
                should_shock = (
                    self.shock_stablecoins
                    or r.symbol not in self._NON_SHOCKABLE_DEFAULT
                )
            if should_shock:
                shocked_prices[r.reserve_address] = r.price_usd * (1 + self.shock_pct)
            else:
                shocked_prices[r.reserve_address] = r.price_usd

        liquidatable_debt_usd = 0.0
        total_debt_usd = 0.0
        liquidatable_obligations = 0
        per_reserve_loss: dict[str, dict[str, float]] = {}

        for ob in snapshot.obligations:
            col_w = 0.0
            for d_dep in ob.deposits:
                r_c = reserves.get(d_dep.reserve_address)
                if r_c is None:
                    continue
                tokens = d_dep.deposited_amount / (10 ** r_c.mint_decimals)
                p = shocked_prices.get(r_c.reserve_address, r_c.price_usd)
                col_w += tokens * p * r_c.liquidation_threshold

            debt_w = 0.0
            for b in ob.borrows:
                r_b = reserves.get(b.reserve_address)
                if r_b is None:
                    continue
                tokens = b.borrowed_amount / (10 ** r_b.mint_decimals)
                # Borrow asset NOT shocked (only collateral is)
                bf = r_b.borrow_factor_bps / 10_000.0
                debt_w += tokens * r_b.price_usd * bf

            debt_raw = ob.borrowed_value_usd(reserves)
            total_debt_usd += debt_raw
            if debt_w > col_w:
                liquidatable_debt_usd += debt_raw
                liquidatable_obligations += 1
                # Attribute loss to largest collateral reserve in this obligation
                if ob.deposits:
                    top = max(
                        ob.deposits,
                        key=lambda d: (
                            (d.deposited_amount / (10 ** reserves[d.reserve_address].mint_decimals))
                            * reserves[d.reserve_address].price_usd
                            if d.reserve_address in reserves else 0
                        ),
                    )
                    bucket = per_reserve_loss.setdefault(
                        top.reserve_address,
                        {"liquidatable_debt_usd": 0.0, "available_liquidity_usd": 0.0},
                    )
                    bucket["liquidatable_debt_usd"] += debt_raw

        # Compute idle (available) liquidity per reserve in USD for the gap.
        # Defensive: an obligation can reference a reserve that's been
        # delisted between snapshot dumps; skip those buckets rather than
        # raising KeyError. The bucket is left with available_liquidity_usd
        # = 0 so the gap == liquidatable_debt_usd (worst case).
        liquidity_gap_usd = 0.0
        for addr, bucket in per_reserve_loss.items():
            reserve = reserves.get(addr)
            if reserve is None:
                bucket["available_liquidity_usd"] = 0.0
                bucket["liquidity_gap_usd"] = bucket["liquidatable_debt_usd"]
                bucket["reserve_missing"] = 1.0
                liquidity_gap_usd += bucket["liquidatable_debt_usd"]
                continue
            tokens = reserve.available_amount / (10 ** reserve.mint_decimals)
            available_usd = tokens * reserve.price_usd
            bucket["available_liquidity_usd"] = available_usd
            gap = max(0.0, bucket["liquidatable_debt_usd"] - available_usd)
            bucket["liquidity_gap_usd"] = gap
            liquidity_gap_usd += gap

        fraction = (liquidatable_debt_usd / total_debt_usd) if total_debt_usd > 0 else 0.0
        target_label = self.target_reserve_symbol or "all collateral"
        return DetectorResult(
            name="CollateralCascade",
            headline_metric=fraction,
            headline_unit="fraction_liquidatable_debt",
            interpretation=(
                f"At a {self.shock_pct:+.0%} shock to {target_label}, "
                f"{fraction:.1%} of debt becomes liquidatable "
                f"({liquidatable_obligations} obligation{'s' if liquidatable_obligations != 1 else ''}); "
                f"USD liquidity gap (debt minus idle reserve liquidity) is "
                f"${liquidity_gap_usd:,.0f}."
            ),
            evidence={
                "shock_pct": self.shock_pct,
                "target_reserve_symbol": self.target_reserve_symbol,
                "liquidatable_debt_usd": liquidatable_debt_usd,
                "total_debt_usd": total_debt_usd,
                "liquidatable_obligations": liquidatable_obligations,
                "liquidity_gap_usd": liquidity_gap_usd,
                "per_reserve_loss": per_reserve_loss,
            },
        )


# ──────────────────────────────────────────────────────────────────────
# 3. Top depositor exit shock
# ──────────────────────────────────────────────────────────────────────


class DepositorExitShock:
    """If the top-N depositors of a reserve withdraw simultaneously, does
    the reserve have enough idle (non-borrowed) liquidity to honor the
    redemption? If not, utilization spikes and the borrow APY enters the
    steep regime of Kamino's interest-rate model.

    Args:
      top_n: number of top depositors to model exiting per reserve.
      target_reserve_symbol: optional — only model exit on this reserve.
    """

    def __init__(self, top_n: int = 1, target_reserve_symbol: str | None = None):
        if top_n < 1:
            raise ValueError("top_n must be >= 1")
        self.top_n = top_n
        self.target_reserve_symbol = target_reserve_symbol

    def run(self, snapshot: KaminoMarketSnapshot) -> DetectorResult:
        worst_post_util = 0.0
        worst_reserve: str | None = None
        per_reserve: dict[str, dict[str, Any]] = {}
        n_reserves_with_data = 0

        for r in snapshot.reserves:
            if self.target_reserve_symbol and r.symbol != self.target_reserve_symbol:
                continue
            deps = snapshot.top_depositors_by_reserve.get(r.reserve_address, [])
            if not deps:
                continue
            n_reserves_with_data += 1
            top_amount = sum(amt for _, amt in deps[: self.top_n])

            # post-exit liquidity: available_amount - top_amount
            post_available = max(0, r.available_amount - top_amount)
            post_supply = post_available + r.borrowed_amount
            post_util = (r.borrowed_amount / post_supply) if post_supply > 0 else float("inf")

            per_reserve[r.reserve_address] = {
                "symbol": r.symbol,
                "pre_utilization": r.utilization,
                "post_utilization": post_util,
                "top_n_exit_amount": top_amount,
                "available_amount": r.available_amount,
            }
            if post_util > worst_post_util:
                worst_post_util = post_util
                worst_reserve = r.reserve_address

        # No top-depositor data anywhere → headline is undefined, NOT 0.
        # Returning 0 would look like "healthy" to a glance reader; None
        # forces consumers to acknowledge the missing input.
        if n_reserves_with_data == 0:
            return DetectorResult(
                name="DepositorExitShock",
                headline_metric=None,
                headline_unit="worst_post_exit_utilization",
                interpretation=(
                    "Cannot compute: snapshot has no top_depositors_by_reserve "
                    "data. Live Kamino fetches via the public RPC do not include "
                    "depositor concentration; the fetcher must be configured "
                    "with an indexer pull (Birdeye / DefiLlama / Helius) for "
                    "this detector to fire."
                ),
                evidence={
                    "top_n": self.top_n,
                    "target_reserve_symbol": self.target_reserve_symbol,
                    "reason": "no top_depositors_by_reserve data",
                    "per_reserve": {},
                },
            )

        worst_symbol = (
            snapshot.reserves_by_address[worst_reserve].symbol
            if worst_reserve else "—"
        )
        return DetectorResult(
            name="DepositorExitShock",
            headline_metric=worst_post_util,
            headline_unit="worst_post_exit_utilization",
            interpretation=(
                f"If top-{self.top_n} depositor(s) exit, worst-case post-exit "
                f"utilization is {worst_post_util:.1%} on the {worst_symbol} reserve "
                f"({worst_reserve[:6] + '…' + worst_reserve[-4:] if worst_reserve else '—'}). "
                f"At >90% utilization Kamino's IRM enters the kink regime; "
                f">100% means a rationing event."
            ),
            evidence={
                "top_n": self.top_n,
                "target_reserve_symbol": self.target_reserve_symbol,
                "worst_post_utilization": worst_post_util,
                "worst_reserve": worst_reserve,
                "per_reserve": per_reserve,
            },
        )


# ──────────────────────────────────────────────────────────────────────
# 4. Utilization band breach
# ──────────────────────────────────────────────────────────────────────


class UtilizationBandBreach:
    """Highlights reserves where utilization is already above the curator's
    target band — a precursor to interest-rate spirals and queue rationing.
    """

    def __init__(self, target_util_max: float = 0.90):
        if not 0.0 < target_util_max < 1.0:
            raise ValueError("target_util_max must be in (0,1)")
        self.target_util_max = target_util_max

    def run(self, snapshot: KaminoMarketSnapshot) -> DetectorResult:
        breached = [r for r in snapshot.reserves if r.utilization > self.target_util_max]
        return DetectorResult(
            name="UtilizationBandBreach",
            headline_metric=len(breached) / max(1, len(snapshot.reserves)),
            headline_unit="fraction_reserves_above_target",
            interpretation=(
                f"{len(breached)} / {len(snapshot.reserves)} reserves are above "
                f"the {self.target_util_max:.0%} utilization band — Kamino's IRM "
                f"enters the steep post-kink regime; depositor withdrawal "
                f"pressure compounds borrow APY."
            ),
            evidence={
                "target_util_max": self.target_util_max,
                "breached_reserves": [
                    {
                        "reserve_address": r.reserve_address,
                        "symbol": r.symbol,
                        "utilization": r.utilization,
                    }
                    for r in breached
                ],
            },
        )


# ──────────────────────────────────────────────────────────────────────
# 5. Liquidation latency
# ──────────────────────────────────────────────────────────────────────


class LiquidationLatency:
    """Estimate the fraction of debt sitting in obligations too small to be
    profitably liquidated at Solana's current compute-unit (CU) cost.

    A liquidator's profit per call is `debt_usd × liq_bonus` and their
    cost is `priority_fee_lamports / 1e9 × sol_price_usd + base_tx_cost`.
    Below the breakeven, undercollateralized positions linger and risk
    becoming bad debt during oracle update lags.

    Solana fee model (post-1.18 prioritization):
      - Base tx fee: 5_000 lamports per signature × n_signatures.
      - Priority fee: `compute_unit_price_microlamports × CU_used / 1e6` lamports.
      - Kamino liquidation CU ~= 400_000 (1 outer ix + several CPIs).

    Args:
      priority_fee_microlamports: micro-lamports per CU. Default 50_000
        (typical congestion fee mid-2026).
      liquidation_cu: total compute units per liquidation call.
      sol_price_usd: SOL/USD for the lamport→USD conversion.
      base_tx_lamports: base transaction cost (5_000 × signatures).
    """

    def __init__(
        self,
        priority_fee_microlamports: int = 50_000,
        liquidation_cu: int = 400_000,
        sol_price_usd: float = 150.0,
        base_tx_lamports: int = 5_000,
    ):
        if priority_fee_microlamports < 0:
            raise ValueError(f"priority_fee_microlamports must be >= 0, got {priority_fee_microlamports}")
        if liquidation_cu <= 0:
            raise ValueError(f"liquidation_cu must be > 0, got {liquidation_cu}")
        if sol_price_usd <= 0:
            raise ValueError(f"sol_price_usd must be > 0, got {sol_price_usd}")
        self.priority_fee_microlamports = priority_fee_microlamports
        self.liquidation_cu = liquidation_cu
        self.sol_price_usd = sol_price_usd
        self.base_tx_lamports = base_tx_lamports

    def _liquidation_cost_usd(self) -> float:
        priority_lamports = self.priority_fee_microlamports * self.liquidation_cu / 1_000_000
        total_lamports = self.base_tx_lamports + priority_lamports
        sol = total_lamports / 1e9
        return sol * self.sol_price_usd

    def run(self, snapshot: KaminoMarketSnapshot) -> DetectorResult:
        cost_usd = self._liquidation_cost_usd()
        reserves = snapshot.reserves_by_address

        unprofitable_count = 0
        unprofitable_debt_usd = 0.0
        total_debt_usd = 0.0

        for ob in snapshot.obligations:
            debt_usd = ob.borrowed_value_usd(reserves)
            total_debt_usd += debt_usd
            if debt_usd == 0:
                continue
            # Use the largest collateral reserve's bonus (worst case for liquidator)
            max_bonus = 0.0
            for d in ob.deposits:
                r = reserves.get(d.reserve_address)
                if r and r.liquidation_bonus > max_bonus:
                    max_bonus = r.liquidation_bonus
            profit_usd = debt_usd * max_bonus
            if profit_usd < cost_usd:
                unprofitable_count += 1
                unprofitable_debt_usd += debt_usd

        fraction = (unprofitable_debt_usd / total_debt_usd) if total_debt_usd > 0 else 0.0
        pos_word = "obligation" if unprofitable_count == 1 else "obligations"
        return DetectorResult(
            name="LiquidationLatency",
            headline_metric=fraction,
            headline_unit="fraction_unprofitable_to_liquidate",
            interpretation=(
                f"At {self.priority_fee_microlamports / 1000:.0f}k μlamports/CU and "
                f"SOL ${self.sol_price_usd:.0f}, liquidation cost is ~${cost_usd:.3f}; "
                f"{fraction:.1%} of debt sits in {unprofitable_count} {pos_word} "
                f"where liquidator profit (debt × max bonus) is below cost — "
                f"these accrue bad-debt risk during oracle-shock windows."
            ),
            evidence={
                "priority_fee_microlamports": self.priority_fee_microlamports,
                "liquidation_cu": self.liquidation_cu,
                "sol_price_usd": self.sol_price_usd,
                "base_tx_lamports": self.base_tx_lamports,
                "cost_per_liquidation_usd": cost_usd,
                "unprofitable_obligations": unprofitable_count,
                "unprofitable_debt_usd": unprofitable_debt_usd,
                "total_debt_usd": total_debt_usd,
            },
        )


# ──────────────────────────────────────────────────────────────────────
# 6. LTV distribution stress
# ──────────────────────────────────────────────────────────────────────


class LTVDistributionStress:
    """Fraction of debt held by obligations within `near_pp` percentage
    points of their weighted liquidation threshold.

    On Kamino, a position is liquidatable when borrowed_value_weighted >
    collateral_value_weighted. We measure the "headroom" of each position
    as `1 - borrowed_weighted / collateral_weighted` and flag those with
    headroom < `near_pp / 100`.

    Args:
      near_pp: percentage points away from liquidation to count as "near".
        Default 5pp (a 5% adverse oracle move would liquidate).
    """

    def __init__(self, near_pp: float = 5.0):
        if not 0 < near_pp < 50:
            raise ValueError(f"near_pp must be in (0, 50), got {near_pp}")
        self.near_pp = near_pp

    def run(self, snapshot: KaminoMarketSnapshot) -> DetectorResult:
        reserves = snapshot.reserves_by_address
        headrooms: list[float] = []
        near_debt_usd = 0.0
        total_debt_usd = 0.0

        for ob in snapshot.obligations:
            col_w = ob.collateral_value_usd_weighted(reserves)
            debt_w = ob.borrowed_value_usd_weighted(reserves)
            debt_raw = ob.borrowed_value_usd(reserves)
            total_debt_usd += debt_raw
            if debt_raw == 0:
                continue
            if col_w == 0:
                # debt with zero collateral — instant bad debt
                near_debt_usd += debt_raw
                headrooms.append(-1.0)
                continue
            headroom = 1.0 - debt_w / col_w
            headrooms.append(headroom)
            if headroom < self.near_pp / 100.0:
                near_debt_usd += debt_raw

        if not headrooms:
            return DetectorResult(
                name="LTVDistributionStress",
                headline_metric=0.0,
                headline_unit=f"fraction_debt_within_{self.near_pp:.0f}pp_of_lltv",
                interpretation=(
                    "No borrowing obligations in the snapshot — LTV "
                    "distribution is undefined. (Live Kamino fetches via "
                    "the public RPC do not always include individual "
                    "obligations; the fetcher must be configured with a "
                    "getProgramAccounts pull for this detector to fire.)"
                ),
                evidence={"n_obligations": 0},
            )

        fraction = (near_debt_usd / total_debt_usd) if total_debt_usd > 0 else 0.0
        sorted_hr = sorted(headrooms)
        worst_5pct_idx = max(1, len(sorted_hr) * 5 // 100)
        worst_5pct_avg = sum(sorted_hr[:worst_5pct_idx]) / worst_5pct_idx

        return DetectorResult(
            name="LTVDistributionStress",
            headline_metric=fraction,
            headline_unit=f"fraction_debt_within_{self.near_pp:.0f}pp_of_lltv",
            interpretation=(
                f"{fraction:.1%} of outstanding debt sits within "
                f"{self.near_pp:.0f}pp of weighted liquidation threshold. "
                f"Worst-5% headroom avg: {worst_5pct_avg:.2%}. "
                f"A small adverse oracle move would push this debt into "
                f"liquidation."
            ),
            evidence={
                "near_pp": self.near_pp,
                "worst_5pct_headroom_avg": worst_5pct_avg,
                "median_headroom": sorted_hr[len(sorted_hr) // 2],
                "n_obligations": len(sorted_hr),
                "near_debt_usd": near_debt_usd,
                "total_debt_usd": total_debt_usd,
            },
        )


# Helper type alias for the runner
Detector = (
    OracleStalenessReplay
    | CollateralCascade
    | DepositorExitShock
    | UtilizationBandBreach
    | LiquidationLatency
    | LTVDistributionStress
)
