"""Tests for diff utilities and synthetic snapshot factory."""

from __future__ import annotations

import math

import pytest

from kvcf.diff import diff_snapshots, summarize_diff
from kvcf.synthetic import make_market_snapshot, make_reserve


def test_diff_two_snapshots_one_slot_apart():
    pre = make_market_snapshot(slot=250_000_000, n_healthy=2)
    post = make_market_snapshot(slot=250_000_500, n_healthy=2)
    md = diff_snapshots(pre, post)
    assert md.slot_delta == 500
    assert len(md.reserves) == 4  # SOL, USDC, JLP, jitoSOL


def test_diff_rejects_market_mismatch():
    pre = make_market_snapshot(slot=1)
    post = make_market_snapshot(slot=2)
    # mutate market_address — KaminoMarketSnapshot is frozen, so use model_copy
    post2 = post.model_copy(update={"market_address": "DIFFERENT_MARKET"})
    with pytest.raises(ValueError, match="market_address"):
        diff_snapshots(pre, post2)


def test_diff_rejects_post_before_pre():
    a = make_market_snapshot(slot=100)
    b = make_market_snapshot(slot=50)
    with pytest.raises(ValueError, match="slot"):
        diff_snapshots(a, b)


def test_summarize_diff_returns_floats():
    pre = make_market_snapshot(slot=1)
    post = make_market_snapshot(slot=2)
    out = summarize_diff(diff_snapshots(pre, post))
    assert all(isinstance(v, float) for v in out.values())
    assert "supply_delta_usd" in out
    assert "max_abs_util_delta" in out


def test_diff_new_reserve_appears_with_zero_pre():
    pre_reserves = make_market_snapshot(slot=1).reserves[:2]  # SOL, USDC only
    pre = make_market_snapshot(slot=1)
    pre = pre.model_copy(update={"reserves": pre_reserves})
    post = make_market_snapshot(slot=2)  # all 4 reserves
    md = diff_snapshots(pre, post)
    # The 2 "new" reserves should have pre_supply=0
    new_reserves = [rd for rd in md.reserves if rd.pre_supply == 0]
    assert len(new_reserves) == 2


def test_diff_price_change_pct():
    sol_pre = make_reserve("SOL", available_amount=1000, borrowed_amount=500,
                            price_override=(100_00_000_000, -8))  # $100
    sol_post = make_reserve("SOL", available_amount=1000, borrowed_amount=500,
                             price_override=(110_00_000_000, -8))  # $110
    from kvcf.state import KaminoMarketSnapshot
    pre = KaminoMarketSnapshot(market_address="m", slot=1, timestamp=1, reserves=[sol_pre])
    post = KaminoMarketSnapshot(market_address="m", slot=2, timestamp=2, reserves=[sol_post])
    md = diff_snapshots(pre, post)
    sol_diff = md.reserves[0]
    assert math.isclose(sol_diff.price_delta_pct, 0.10, abs_tol=1e-9)


# ──────────────────────────────────────────────────────────────────────
# Synthetic factory
# ──────────────────────────────────────────────────────────────────────


def test_synthetic_snapshot_has_expected_reserves():
    snap = make_market_snapshot()
    symbols = {r.symbol for r in snap.reserves}
    assert symbols == {"SOL", "USDC", "JLP", "jitoSOL"}


def test_synthetic_snapshot_obligation_counts():
    snap = make_market_snapshot(n_healthy=3, n_at_risk=2, n_underwater=1)
    assert len(snap.obligations) == 6


def test_synthetic_snapshot_deterministic_per_seed():
    a = make_market_snapshot(seed=42, n_healthy=5)
    b = make_market_snapshot(seed=42, n_healthy=5)
    # owner addresses should match
    owners_a = [ob.owner for ob in a.obligations]
    owners_b = [ob.owner for ob in b.obligations]
    assert owners_a == owners_b


def test_synthetic_top_depositors_present_for_sol_usdc():
    snap = make_market_snapshot()
    assert len(snap.top_depositors_by_reserve) >= 2
    # SOL whale > 0
    sol_addr = snap.reserves[0].reserve_address
    assert snap.top_depositors_by_reserve[sol_addr][0][1] > 0
