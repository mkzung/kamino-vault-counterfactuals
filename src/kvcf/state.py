"""Domain models for Kamino Lend reserve and obligation state on Solana.

Kamino Lend is one of the largest lending protocols on Solana by TVL.
Each Reserve is a pool for one asset, with on-chain config (LTV, liquidation
threshold, liquidation bonus, max-borrow factor, kink utilization). Reserves
are read/written by Obligations — borrower positions holding deposits and
borrows across one or more reserves on the same chain.

Risk evaluation needs four pieces of state:

  1. Reserve config: LTV, liquidation_threshold_bps, liquidation_bonus_bps,
     borrow_factor_bps, oracle source, oracle_price + exp.
  2. Reserve liquidity: available, borrowed, total supply (= available + borrowed
     + accumulated_referrer_fees), borrow APY, deposit APY.
  3. Obligation breakdown: per-borrower aggregate collateral_value_usd and
     borrowed_value_usd; per-reserve deposit and borrow amounts.
  4. Time-series of (1)+(2)+(3) at successive slot snapshots.

This module mirrors morpho-vault-counterfactuals/src/mvcf/state.py but on the
Solana account model: prices are stored as (value, exp) pairs in Pyth/Scope
convention rather than Morpho's 36-decimal fixed-point.

References:
  - Kamino Lend repo: github.com/Kamino-Finance/klend
  - Scope pricing engine: github.com/Kamino-Finance/scope
  - Kamino docs: docs.kamino.finance
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


def _price_to_units_per_token(value: int, exp: int) -> float:
    """Convert a Pyth/Scope (value, exp) price to a float USD-per-token.

    Pyth convention: price = value * 10^(exp). `exp` is usually negative
    (e.g., SOL/USD at $150 might be value=15_000_000_000, exp=-8 →
    150.0). We accept any signed exp.
    """
    return float(value) * (10.0 ** exp)


class ReserveState(BaseModel):
    """A single Kamino Lend reserve (one asset) at one Solana slot.

    All deposit / borrow amounts are in the asset's smallest unit
    (e.g., USDC mint_decimals=6 → 1_000_000 == 1 USDC; SOL is 1e9 lamports).
    Prices are in Pyth/Scope (value, exp) convention to avoid lossy float
    conversion at fetch time.
    """

    model_config = ConfigDict(frozen=True)

    reserve_address: str = Field(..., description="Solana account address (base58)")
    slot: int = Field(..., ge=0, description="Solana slot")
    timestamp: int = Field(..., description="Unix seconds")
    mint: str = Field(..., description="Token mint address (base58)")
    symbol: str = Field(..., description="Token symbol, e.g. SOL, USDC, jitoSOL")
    mint_decimals: int = Field(..., ge=0, le=18)

    # Liquidity state (in mint smallest units)
    available_amount: int = Field(..., ge=0, description="liquidity sitting in reserve")
    borrowed_amount: int = Field(..., ge=0, description="outstanding borrows")
    accumulated_protocol_fees: int = Field(default=0, ge=0)
    accumulated_referrer_fees: int = Field(default=0, ge=0)

    # Config — bps = basis points, 10000 = 100%
    loan_to_value_pct: int = Field(..., ge=0, le=100, description="% of collateral usable as LTV cap")
    liquidation_threshold_pct: int = Field(..., ge=0, le=100, description="% LTV that triggers liquidation (LLTV equivalent)")
    liquidation_bonus_bps: int = Field(..., ge=0, le=5000, description="liquidator bonus on seized collateral in bps")
    borrow_factor_bps: int = Field(default=10000, ge=10000, le=100000, description="borrow factor — kamino multiplies borrow value by this for risk calc (≥1.0)")
    max_liquidatable_close_factor_pct: int = Field(default=20, ge=0, le=100)
    deposit_limit: int = Field(default=0, ge=0)
    borrow_limit: int = Field(default=0, ge=0)

    # Oracle
    oracle_price_value: int = Field(..., ge=0)
    oracle_price_exp: int = Field(..., description="signed exponent; price = value * 10^exp")
    oracle_last_update_slot: int = Field(..., ge=0)
    oracle_source: str = Field(default="pyth", description="pyth | switchboard | scope")

    @property
    def total_supply(self) -> int:
        """Total reserve supply = available + borrowed (fees excluded — they're protocol-owned)."""
        return self.available_amount + self.borrowed_amount

    @property
    def utilization(self) -> float:
        """Borrow / supply utilization.

        Normally in [0, 1]. Values > 1 should not occur on Kamino because
        borrows are always backed by available; if it does, treat as
        protocol-invariant violation. supply=0 returns 0.0 if borrows=0;
        otherwise inf.
        """
        if self.total_supply == 0:
            return float("inf") if self.borrowed_amount > 0 else 0.0
        return self.borrowed_amount / self.total_supply

    @property
    def liquidation_threshold(self) -> float:
        """Liquidation threshold as a float in [0,1]."""
        return self.liquidation_threshold_pct / 100.0

    @property
    def loan_to_value(self) -> float:
        """Initial LTV cap as a float in [0,1]."""
        return self.loan_to_value_pct / 100.0

    @property
    def liquidation_bonus(self) -> float:
        """Liquidation bonus as a float in [0,1]."""
        return self.liquidation_bonus_bps / 10_000.0

    @property
    def price_usd(self) -> float:
        """Convenience accessor; never used for math, only display."""
        return _price_to_units_per_token(self.oracle_price_value, self.oracle_price_exp)

    def oracle_staleness_slots(self, current_slot: int) -> int:
        """Number of slots since the oracle last updated.

        Solana slot time ≈ 400 ms. Kamino's Scope oracle pause threshold
        is typically 25 slots (10 seconds) for major assets and 50-150
        slots for slower feeds.
        """
        return max(0, current_slot - self.oracle_last_update_slot)


class ObligationDeposit(BaseModel):
    """One reserve deposit inside a borrower's obligation."""

    model_config = ConfigDict(frozen=True)

    reserve_address: str
    deposited_amount: int = Field(..., ge=0)


class ObligationBorrow(BaseModel):
    """One reserve borrow inside a borrower's obligation."""

    model_config = ConfigDict(frozen=True)

    reserve_address: str
    borrowed_amount: int = Field(..., ge=0)


class ObligationState(BaseModel):
    """A single Kamino Lend obligation (one borrower position).

    A Kamino obligation can have multiple deposits AND multiple borrows
    in the same account (unlike Morpho Blue's single-market positions).
    Health is computed across the full basket using each reserve's
    liquidation_threshold.
    """

    model_config = ConfigDict(frozen=True)

    obligation_address: str
    owner: str = Field(..., description="borrower wallet")
    slot: int = Field(..., ge=0)
    deposits: list[ObligationDeposit] = Field(default_factory=list)
    borrows: list[ObligationBorrow] = Field(default_factory=list)

    def collateral_value_usd(self, reserves_by_address: dict[str, ReserveState]) -> float:
        """Sum of deposited collateral × oracle price across reserves."""
        total = 0.0
        for d in self.deposits:
            r = reserves_by_address.get(d.reserve_address)
            if r is None:
                continue
            tokens = d.deposited_amount / (10 ** r.mint_decimals)
            total += tokens * r.price_usd
        return total

    def collateral_value_usd_weighted(
        self, reserves_by_address: dict[str, ReserveState]
    ) -> float:
        """Collateral weighted by each reserve's LIQUIDATION threshold.

        This is the value that must exceed borrowed_value_usd_weighted
        for the position to be healthy.
        """
        total = 0.0
        for d in self.deposits:
            r = reserves_by_address.get(d.reserve_address)
            if r is None:
                continue
            tokens = d.deposited_amount / (10 ** r.mint_decimals)
            total += tokens * r.price_usd * r.liquidation_threshold
        return total

    def borrowed_value_usd(self, reserves_by_address: dict[str, ReserveState]) -> float:
        """Sum of borrowed amount × oracle price (raw debt, no borrow factor)."""
        total = 0.0
        for b in self.borrows:
            r = reserves_by_address.get(b.reserve_address)
            if r is None:
                continue
            tokens = b.borrowed_amount / (10 ** r.mint_decimals)
            total += tokens * r.price_usd
        return total

    def borrowed_value_usd_weighted(
        self, reserves_by_address: dict[str, ReserveState]
    ) -> float:
        """Borrowed value × per-reserve borrow_factor — Kamino's risk-side adjustment."""
        total = 0.0
        for b in self.borrows:
            r = reserves_by_address.get(b.reserve_address)
            if r is None:
                continue
            tokens = b.borrowed_amount / (10 ** r.mint_decimals)
            bf = r.borrow_factor_bps / 10_000.0
            total += tokens * r.price_usd * bf
        return total

    def ltv(self, reserves_by_address: dict[str, ReserveState]) -> float:
        """Loan-to-value across the basket.

        On Kamino a position is liquidatable when borrowed_weighted >
        collateral_weighted, i.e. when ltv > 1.0 in the *weighted* sense.
        We return the unweighted LTV (debt / collateral) for parity with
        Morpho's LTV semantics, so a downstream detector can compare to
        per-reserve liquidation_threshold.
        """
        col = self.collateral_value_usd(reserves_by_address)
        debt = self.borrowed_value_usd(reserves_by_address)
        if col == 0:
            return float("inf") if debt > 0 else 0.0
        return debt / col

    def is_liquidatable(self, reserves_by_address: dict[str, ReserveState]) -> bool:
        """Position liquidatable iff weighted-borrowed > weighted-collateral."""
        col_w = self.collateral_value_usd_weighted(reserves_by_address)
        debt_w = self.borrowed_value_usd_weighted(reserves_by_address)
        return debt_w > col_w


class KaminoMarketSnapshot(BaseModel):
    """A whole Kamino Lend market at a single Solana slot.

    Mirrors mvcf.VaultSnapshot. Aggregates reserve state + a sample of
    obligation positions. The "market" here is one Kamino lending market
    (e.g., the Main market or the JLP market); each market has many
    reserves and many obligations.
    """

    model_config = ConfigDict(frozen=True)

    market_address: str
    market_name: str = Field(default="", description="Human label, e.g. 'Main Market'")
    slot: int = Field(..., ge=0)
    timestamp: int = Field(...)
    reserves: list[ReserveState] = Field(default_factory=list)
    obligations: list[ObligationState] = Field(default_factory=list)
    # Top depositors per reserve, optional, for HHI computation
    top_depositors_by_reserve: dict[str, list[tuple[str, int]]] = Field(
        default_factory=dict,
        description="reserve_address → [(depositor, amount), ...] sorted desc",
    )

    @property
    def reserves_by_address(self) -> dict[str, ReserveState]:
        return {r.reserve_address: r for r in self.reserves}

    @property
    def total_supply_usd(self) -> float:
        out = 0.0
        for r in self.reserves:
            tokens = r.total_supply / (10 ** r.mint_decimals)
            out += tokens * r.price_usd
        return out

    @property
    def total_borrowed_usd(self) -> float:
        out = 0.0
        for r in self.reserves:
            tokens = r.borrowed_amount / (10 ** r.mint_decimals)
            out += tokens * r.price_usd
        return out

    def reserve_hhi(self, reserve_address: str) -> float:
        """HHI of depositor concentration in one reserve, in [0,1]."""
        deps = self.top_depositors_by_reserve.get(reserve_address, [])
        if not deps:
            return 0.0
        total = sum(amt for _, amt in deps)
        if total == 0:
            return 0.0
        return sum((amt / total) ** 2 for _, amt in deps)


@dataclass
class MarketHistory:
    """Ordered series of KaminoMarketSnapshots for one market.

    Immutable replay log — detectors read but don't mutate.
    """

    market_address: str
    snapshots: list[KaminoMarketSnapshot] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.snapshots)

    def by_slot(self, slot: int) -> KaminoMarketSnapshot | None:
        for s in self.snapshots:
            if s.slot == slot:
                return s
        return None

    def latest(self) -> KaminoMarketSnapshot:
        if not self.snapshots:
            raise ValueError("MarketHistory is empty")
        return max(self.snapshots, key=lambda s: s.slot)

    def iter_pairs(self) -> Iterable[tuple[KaminoMarketSnapshot, KaminoMarketSnapshot]]:
        """Yield (prev, next) consecutive snapshots ordered by slot."""
        ordered = sorted(self.snapshots, key=lambda s: s.slot)
        for i in range(len(ordered) - 1):
            yield ordered[i], ordered[i + 1]
