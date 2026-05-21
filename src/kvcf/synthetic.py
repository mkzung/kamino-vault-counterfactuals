"""Deterministic synthetic snapshot generators for tests and offline demos.

`make_market_snapshot()` produces a realistic Kamino-shaped state with a
configurable mix of healthy + at-risk obligations. Used by every test in
tests/test_detectors.py — no live RPC needed.

The generator is deterministic given (seed, n_obligations), so test
fixtures stay stable across CI runs.
"""

from __future__ import annotations

import hashlib

from .state import (
    KaminoMarketSnapshot,
    ObligationBorrow,
    ObligationDeposit,
    ObligationState,
    ReserveState,
)

# Canonical reserve addresses (mainnet Kamino Main market, abbreviated)
SOL_RESERVE = "d4A2prbA2whesmvHaL88BH6Ewn5N4bTSU2Ze8P6Bc4Q"
USDC_RESERVE = "Ga4rZytCpq1unD4DbEJ5bkHeUz9g3oh9AAFEi6vSauXp"
JLP_RESERVE = "DdTmCCjv7zHRD1hJv3E8bpnSEQBzdKkzB1j9ApXX5QrQ"
JITOSOL_RESERVE = "EVbyPKrHG6WBfm4dLxLMJpUDY43cCAcHSpV3KYjKsktW"

# Reasonable mainnet prices for synthetic state
_PRICE_DEFAULTS = {
    "SOL": (150_000_000_00, -8),       # $150.00, exp -8
    "USDC": (1_00_000_000, -8),         # $1.0000
    "JLP": (4_500_000_00, -8),          # $4.50
    "jitoSOL": (175_000_000_00, -8),    # $175.00
}


def _addr_for_symbol(symbol: str) -> str:
    return {
        "SOL": SOL_RESERVE,
        "USDC": USDC_RESERVE,
        "JLP": JLP_RESERVE,
        "jitoSOL": JITOSOL_RESERVE,
    }.get(symbol, "Unknown1111111111111111111111111111111111111")


def _decimals_for_symbol(symbol: str) -> int:
    return {
        "SOL": 9,
        "USDC": 6,
        "JLP": 6,
        "jitoSOL": 9,
    }.get(symbol, 6)


def make_reserve(
    symbol: str,
    available_amount: int,
    borrowed_amount: int,
    *,
    slot: int = 250_000_000,
    timestamp: int = 1_716_000_000,
    loan_to_value_pct: int = 70,
    liquidation_threshold_pct: int = 75,
    liquidation_bonus_bps: int = 500,
    borrow_factor_bps: int = 10_000,
    oracle_staleness_slots: int = 0,
    price_override: tuple[int, int] | None = None,
) -> ReserveState:
    """Build a ReserveState with realistic Kamino-shape defaults."""
    price_value, price_exp = price_override or _PRICE_DEFAULTS.get(symbol, (1_00_000_000, -8))
    return ReserveState(
        reserve_address=_addr_for_symbol(symbol),
        slot=slot,
        timestamp=timestamp,
        mint=hashlib.sha256(f"mint-{symbol}".encode()).hexdigest()[:43],
        symbol=symbol,
        mint_decimals=_decimals_for_symbol(symbol),
        available_amount=available_amount,
        borrowed_amount=borrowed_amount,
        loan_to_value_pct=loan_to_value_pct,
        liquidation_threshold_pct=liquidation_threshold_pct,
        liquidation_bonus_bps=liquidation_bonus_bps,
        borrow_factor_bps=borrow_factor_bps,
        oracle_price_value=price_value,
        oracle_price_exp=price_exp,
        oracle_last_update_slot=slot - oracle_staleness_slots,
        oracle_source="pyth",
    )


def make_obligation(
    owner_id: int,
    deposits: list[tuple[str, int]],
    borrows: list[tuple[str, int]],
    *,
    slot: int = 250_000_000,
) -> ObligationState:
    """Build an ObligationState from (symbol, amount) lists."""
    return ObligationState(
        obligation_address=hashlib.sha256(f"ob-{owner_id}".encode()).hexdigest()[:43],
        owner=hashlib.sha256(f"owner-{owner_id}".encode()).hexdigest()[:43],
        slot=slot,
        deposits=[
            ObligationDeposit(reserve_address=_addr_for_symbol(sym), deposited_amount=amt)
            for sym, amt in deposits
        ],
        borrows=[
            ObligationBorrow(reserve_address=_addr_for_symbol(sym), borrowed_amount=amt)
            for sym, amt in borrows
        ],
    )


def make_market_snapshot(
    *,
    seed: int = 0,
    n_healthy: int = 5,
    n_at_risk: int = 2,
    n_underwater: int = 0,
    slot: int = 250_000_000,
    timestamp: int = 1_716_000_000,
) -> KaminoMarketSnapshot:
    """Build a realistic synthetic snapshot.

    healthy obligations: ~30% LTV, lots of headroom
    at-risk obligations: ~70% LTV, within a 5pp slip of liquidation
    underwater obligations: >75% LTV, instant-liquidatable
    """
    reserves = [
        make_reserve(
            "SOL",
            available_amount=80_000 * 10**9,   # 80k SOL idle
            borrowed_amount=40_000 * 10**9,    # 40k SOL borrowed
            slot=slot,
            timestamp=timestamp,
            liquidation_bonus_bps=500,         # 5% bonus
        ),
        make_reserve(
            "USDC",
            available_amount=15_000_000 * 10**6,   # 15M USDC
            borrowed_amount=8_000_000 * 10**6,     # 8M USDC borrowed
            slot=slot,
            timestamp=timestamp,
            liquidation_bonus_bps=300,             # 3% bonus
            loan_to_value_pct=85,
            liquidation_threshold_pct=88,
        ),
        make_reserve(
            "JLP",
            available_amount=6_000_000 * 10**6,
            borrowed_amount=2_500_000 * 10**6,
            slot=slot,
            timestamp=timestamp,
            liquidation_bonus_bps=700,             # 7% bonus, riskier
            loan_to_value_pct=55,
            liquidation_threshold_pct=60,
        ),
        make_reserve(
            "jitoSOL",
            available_amount=10_000 * 10**9,
            borrowed_amount=3_000 * 10**9,
            slot=slot,
            timestamp=timestamp,
            liquidation_bonus_bps=600,
        ),
    ]
    obligations: list[ObligationState] = []

    # Healthy: deposit ~3x debt value
    # Use a known seed-deterministic scaling
    for i in range(n_healthy):
        # 100 SOL collateral + 5000 USDC borrowed → at $150 SOL = $15k col, $5k debt → 33% LTV
        scale = 1 + ((seed + i) % 4)
        obligations.append(
            make_obligation(
                owner_id=seed * 1000 + i,
                deposits=[("SOL", 100 * scale * 10**9)],
                borrows=[("USDC", 5000 * scale * 10**6)],
                slot=slot,
            )
        )

    # At-risk: ~70% LTV
    # 100 SOL ($15k) col + 10_500 USDC borrowed → 70% LTV (just under 75% LT)
    for i in range(n_at_risk):
        scale = 1 + ((seed + i) % 3)
        obligations.append(
            make_obligation(
                owner_id=seed * 1000 + n_healthy + i,
                deposits=[("SOL", 100 * scale * 10**9)],
                borrows=[("USDC", 10_500 * scale * 10**6)],
                slot=slot,
            )
        )

    # Underwater: >75% LTV
    # 100 SOL ($15k) col + 12_500 USDC borrowed → ~83% LTV (above SOL's 75% LT)
    for i in range(n_underwater):
        scale = 1 + ((seed + i) % 2)
        obligations.append(
            make_obligation(
                owner_id=seed * 1000 + n_healthy + n_at_risk + i,
                deposits=[("SOL", 100 * scale * 10**9)],
                borrows=[("USDC", 12_500 * scale * 10**6)],
                slot=slot,
            )
        )

    # Synthetic top depositors per reserve (one whale, several small fish)
    top_depositors_by_reserve: dict[str, list[tuple[str, int]]] = {
        SOL_RESERVE: [
            (hashlib.sha256(f"sol-whale-{seed}".encode()).hexdigest()[:43], 30_000 * 10**9),
            (hashlib.sha256(f"sol-w2-{seed}".encode()).hexdigest()[:43], 10_000 * 10**9),
            (hashlib.sha256(f"sol-w3-{seed}".encode()).hexdigest()[:43], 5_000 * 10**9),
        ],
        USDC_RESERVE: [
            (hashlib.sha256(f"usdc-whale-{seed}".encode()).hexdigest()[:43], 5_000_000 * 10**6),
            (hashlib.sha256(f"usdc-w2-{seed}".encode()).hexdigest()[:43], 2_000_000 * 10**6),
        ],
    }

    return KaminoMarketSnapshot(
        market_address="7u3HeHxYDLhnCoErrtycNokbQYbWGzLs6JSDqGAv5K1z",  # Kamino Main market
        market_name="Kamino Main Market (synthetic)",
        slot=slot,
        timestamp=timestamp,
        reserves=reserves,
        obligations=obligations,
        top_depositors_by_reserve=top_depositors_by_reserve,
    )
