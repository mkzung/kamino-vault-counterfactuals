"""Tests for state models — ReserveState, ObligationState, KaminoMarketSnapshot."""

from __future__ import annotations

import math

import pytest

from kvcf.state import (
    KaminoMarketSnapshot,
    MarketHistory,
    ObligationBorrow,
    ObligationDeposit,
    ObligationState,
    ReserveState,
)


def _sol_reserve(**overrides) -> ReserveState:
    defaults = dict(
        reserve_address="d4A2prbA2whesmvHaL88BH6Ewn5N4bTSU2Ze8P6Bc4Q",
        slot=250_000_000,
        timestamp=1_716_000_000,
        mint="So11111111111111111111111111111111111111112",
        symbol="SOL",
        mint_decimals=9,
        available_amount=80_000 * 10**9,
        borrowed_amount=40_000 * 10**9,
        loan_to_value_pct=70,
        liquidation_threshold_pct=75,
        liquidation_bonus_bps=500,
        oracle_price_value=150_000_000_00,
        oracle_price_exp=-8,
        oracle_last_update_slot=250_000_000,
    )
    defaults.update(overrides)
    return ReserveState(**defaults)


def _usdc_reserve(**overrides) -> ReserveState:
    defaults = dict(
        reserve_address="Ga4rZytCpq1unD4DbEJ5bkHeUz9g3oh9AAFEi6vSauXp",
        slot=250_000_000,
        timestamp=1_716_000_000,
        mint="EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        symbol="USDC",
        mint_decimals=6,
        available_amount=15_000_000 * 10**6,
        borrowed_amount=8_000_000 * 10**6,
        loan_to_value_pct=85,
        liquidation_threshold_pct=88,
        liquidation_bonus_bps=300,
        oracle_price_value=1_00_000_000,
        oracle_price_exp=-8,
        oracle_last_update_slot=250_000_000,
    )
    defaults.update(overrides)
    return ReserveState(**defaults)


class TestReserveState:
    def test_total_supply_equals_available_plus_borrowed(self):
        r = _sol_reserve()
        assert r.total_supply == 120_000 * 10**9

    def test_utilization_normal(self):
        r = _sol_reserve()
        # 40_000 / 120_000 = 0.333...
        assert math.isclose(r.utilization, 1 / 3, abs_tol=1e-12)

    def test_utilization_zero_supply_zero_borrow(self):
        r = _sol_reserve(available_amount=0, borrowed_amount=0)
        assert r.utilization == 0.0

    def test_utilization_zero_supply_with_borrow_is_inf(self):
        # available=0, borrowed=0 returns total_supply=0 and borrow=0 → 0.
        # Force borrowed>0 with available=0 (which would mean total_supply=borrowed) — invariant: u=1.0.
        r = _sol_reserve(available_amount=0, borrowed_amount=100 * 10**9)
        assert r.utilization == 1.0  # 100 / 100

    def test_price_usd(self):
        r = _sol_reserve()
        assert math.isclose(r.price_usd, 150.0)

    def test_oracle_staleness_slots(self):
        r = _sol_reserve(oracle_last_update_slot=249_999_950)
        assert r.oracle_staleness_slots(250_000_000) == 50

    def test_oracle_staleness_negative_clamped_to_zero(self):
        r = _sol_reserve(oracle_last_update_slot=250_000_010)
        assert r.oracle_staleness_slots(250_000_000) == 0

    def test_liquidation_threshold_float(self):
        r = _sol_reserve(liquidation_threshold_pct=75)
        assert math.isclose(r.liquidation_threshold, 0.75)

    def test_immutable(self):
        from pydantic import ValidationError

        r = _sol_reserve()
        with pytest.raises(ValidationError):
            r.symbol = "JLP"  # type: ignore[misc]

    def test_validation_rejects_pct_out_of_range(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _sol_reserve(liquidation_threshold_pct=120)

    def test_extra_forbid_rejects_rogue_field(self):
        # v0.2.0: extra='forbid' on every model — catches typos in
        # field names and stale/upgraded fixtures that ship unknown keys.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _sol_reserve(rogue_field="x")  # type: ignore[call-arg]


class TestExtraForbidOnAllModels:
    """v0.2.0: every public model rejects unknown fields."""

    def test_obligation_deposit_rejects_rogue(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ObligationDeposit(  # type: ignore[call-arg]
                reserve_address="r", deposited_amount=1, rogue="x"
            )

    def test_obligation_borrow_rejects_rogue(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ObligationBorrow(  # type: ignore[call-arg]
                reserve_address="r", borrowed_amount=1, rogue="x"
            )

    def test_obligation_state_rejects_rogue(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ObligationState(  # type: ignore[call-arg]
                obligation_address="ob",
                owner="w",
                slot=1,
                rogue_field="x",
            )

    def test_kamino_market_snapshot_rejects_rogue(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            KaminoMarketSnapshot(  # type: ignore[call-arg]
                market_address="m",
                slot=1,
                timestamp=2,
                reserves=[],
                obligations=[],
                rogue_field="x",
            )


class TestObligationState:
    def test_collateral_value_usd(self):
        sol = _sol_reserve()
        usdc = _usdc_reserve()
        reserves = {sol.reserve_address: sol, usdc.reserve_address: usdc}
        ob = ObligationState(
            obligation_address="ob1",
            owner="w1",
            slot=250_000_000,
            deposits=[
                ObligationDeposit(reserve_address=sol.reserve_address, deposited_amount=100 * 10**9)
            ],
            borrows=[],
        )
        # 100 SOL × $150 = $15,000
        assert math.isclose(ob.collateral_value_usd(reserves), 15_000.0)

    def test_collateral_value_usd_weighted(self):
        sol = _sol_reserve(liquidation_threshold_pct=75)
        reserves = {sol.reserve_address: sol}
        ob = ObligationState(
            obligation_address="ob1",
            owner="w1",
            slot=250_000_000,
            deposits=[
                ObligationDeposit(reserve_address=sol.reserve_address, deposited_amount=100 * 10**9)
            ],
            borrows=[],
        )
        # 100 SOL × $150 × 0.75 = $11,250
        assert math.isclose(ob.collateral_value_usd_weighted(reserves), 11_250.0)

    def test_borrowed_value_usd_weighted_with_borrow_factor(self):
        usdc = _usdc_reserve(borrow_factor_bps=12_000)  # 1.2x risk weight
        reserves = {usdc.reserve_address: usdc}
        ob = ObligationState(
            obligation_address="ob1",
            owner="w1",
            slot=250_000_000,
            deposits=[],
            borrows=[
                ObligationBorrow(reserve_address=usdc.reserve_address, borrowed_amount=10_000 * 10**6)
            ],
        )
        # 10_000 USDC × $1 × 1.2 = 12_000
        assert math.isclose(ob.borrowed_value_usd_weighted(reserves), 12_000.0)

    def test_ltv_healthy(self):
        sol = _sol_reserve()
        usdc = _usdc_reserve()
        reserves = {sol.reserve_address: sol, usdc.reserve_address: usdc}
        # 100 SOL ($15k) collateral, 5000 USDC debt → 33.3% LTV
        ob = ObligationState(
            obligation_address="ob1",
            owner="w1",
            slot=250_000_000,
            deposits=[
                ObligationDeposit(reserve_address=sol.reserve_address, deposited_amount=100 * 10**9)
            ],
            borrows=[
                ObligationBorrow(reserve_address=usdc.reserve_address, borrowed_amount=5000 * 10**6)
            ],
        )
        assert math.isclose(ob.ltv(reserves), 5000 / 15_000, abs_tol=1e-9)
        assert ob.is_liquidatable(reserves) is False

    def test_ltv_zero_collateral_with_debt_is_inf(self):
        usdc = _usdc_reserve()
        reserves = {usdc.reserve_address: usdc}
        ob = ObligationState(
            obligation_address="ob1",
            owner="w1",
            slot=250_000_000,
            deposits=[],
            borrows=[
                ObligationBorrow(reserve_address=usdc.reserve_address, borrowed_amount=100 * 10**6)
            ],
        )
        assert ob.ltv(reserves) == float("inf")
        assert ob.is_liquidatable(reserves) is True

    def test_is_liquidatable_when_weighted_debt_exceeds_collateral(self):
        sol = _sol_reserve(liquidation_threshold_pct=75)
        usdc = _usdc_reserve()
        reserves = {sol.reserve_address: sol, usdc.reserve_address: usdc}
        # 100 SOL ($15k) col × 0.75 LT = $11,250 weighted col
        # 12_000 USDC debt × 1.0 BF = $12,000 weighted debt → liquidatable
        ob = ObligationState(
            obligation_address="ob1",
            owner="w1",
            slot=250_000_000,
            deposits=[
                ObligationDeposit(reserve_address=sol.reserve_address, deposited_amount=100 * 10**9)
            ],
            borrows=[
                ObligationBorrow(reserve_address=usdc.reserve_address, borrowed_amount=12_000 * 10**6)
            ],
        )
        assert ob.is_liquidatable(reserves) is True


class TestMarketSnapshot:
    def test_reserves_by_address_lookup(self):
        sol = _sol_reserve()
        snap = KaminoMarketSnapshot(
            market_address="mkt",
            slot=1,
            timestamp=2,
            reserves=[sol],
            obligations=[],
        )
        assert snap.reserves_by_address[sol.reserve_address] is sol

    def test_total_supply_usd(self):
        sol = _sol_reserve()
        usdc = _usdc_reserve()
        snap = KaminoMarketSnapshot(
            market_address="mkt",
            slot=1,
            timestamp=2,
            reserves=[sol, usdc],
        )
        expected = (120_000 * 150) + (23_000_000 * 1)
        assert math.isclose(snap.total_supply_usd, expected)

    def test_reserve_hhi_no_deposits_returns_zero(self):
        sol = _sol_reserve()
        snap = KaminoMarketSnapshot(
            market_address="mkt",
            slot=1,
            timestamp=2,
            reserves=[sol],
        )
        assert snap.reserve_hhi(sol.reserve_address) == 0.0

    def test_reserve_hhi_one_whale(self):
        sol = _sol_reserve()
        snap = KaminoMarketSnapshot(
            market_address="mkt",
            slot=1,
            timestamp=2,
            reserves=[sol],
            top_depositors_by_reserve={sol.reserve_address: [("w1", 100), ("w2", 0)]},
        )
        # one depositor owns all → HHI = 1
        assert math.isclose(snap.reserve_hhi(sol.reserve_address), 1.0)

    def test_reserve_hhi_uniform(self):
        sol = _sol_reserve()
        snap = KaminoMarketSnapshot(
            market_address="mkt",
            slot=1,
            timestamp=2,
            reserves=[sol],
            top_depositors_by_reserve={sol.reserve_address: [("w1", 25), ("w2", 25), ("w3", 25), ("w4", 25)]},
        )
        # 4 equal depositors → HHI = 4 × 0.25² = 0.25
        assert math.isclose(snap.reserve_hhi(sol.reserve_address), 0.25)


class TestMarketHistory:
    def test_iter_pairs_yields_consecutive_in_slot_order(self):
        snaps = [
            KaminoMarketSnapshot(market_address="m", slot=s, timestamp=s, reserves=[], obligations=[])
            for s in [300, 100, 200]
        ]
        hist = MarketHistory(market_address="m", snapshots=snaps)
        pairs = list(hist.iter_pairs())
        assert [(p.slot, n.slot) for p, n in pairs] == [(100, 200), (200, 300)]

    def test_latest_returns_max_slot(self):
        snaps = [
            KaminoMarketSnapshot(market_address="m", slot=s, timestamp=s, reserves=[], obligations=[])
            for s in [100, 300, 200]
        ]
        hist = MarketHistory(market_address="m", snapshots=snaps)
        assert hist.latest().slot == 300

    def test_latest_raises_on_empty(self):
        hist = MarketHistory(market_address="m", snapshots=[])
        with pytest.raises(ValueError):
            hist.latest()

    def test_by_slot_returns_none_when_missing(self):
        hist = MarketHistory(market_address="m", snapshots=[])
        assert hist.by_slot(123) is None
