"""Snapshot-to-snapshot diff utilities.

Mirrors mvcf.diff: given two KaminoMarketSnapshots, compute deltas in
reserve liquidity, utilization, and obligation health that a curator
should review weekly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import KaminoMarketSnapshot


@dataclass(frozen=True)
class ReserveDiff:
    reserve_address: str
    symbol: str
    pre_supply: int
    post_supply: int
    pre_borrow: int
    post_borrow: int
    pre_util: float
    post_util: float
    pre_price_usd: float
    post_price_usd: float

    @property
    def supply_delta(self) -> int:
        return self.post_supply - self.pre_supply

    @property
    def borrow_delta(self) -> int:
        return self.post_borrow - self.pre_borrow

    @property
    def util_delta(self) -> float:
        return self.post_util - self.pre_util

    @property
    def price_delta_pct(self) -> float:
        if self.pre_price_usd == 0:
            return 0.0
        return (self.post_price_usd - self.pre_price_usd) / self.pre_price_usd


@dataclass(frozen=True)
class MarketDiff:
    market_address: str
    pre_slot: int
    post_slot: int
    pre_timestamp: int
    post_timestamp: int
    reserves: list[ReserveDiff]
    total_supply_usd_pre: float
    total_supply_usd_post: float
    total_borrowed_usd_pre: float
    total_borrowed_usd_post: float

    @property
    def slot_delta(self) -> int:
        return self.post_slot - self.pre_slot

    @property
    def supply_delta_usd(self) -> float:
        return self.total_supply_usd_post - self.total_supply_usd_pre

    @property
    def borrow_delta_usd(self) -> float:
        return self.total_borrowed_usd_post - self.total_borrowed_usd_pre


def diff_snapshots(pre: KaminoMarketSnapshot, post: KaminoMarketSnapshot) -> MarketDiff:
    """Compute per-reserve and market-aggregate deltas between two snapshots."""
    if pre.market_address != post.market_address:
        raise ValueError(
            f"market_address mismatch: pre={pre.market_address} post={post.market_address}"
        )
    if post.slot < pre.slot:
        raise ValueError(f"post.slot {post.slot} < pre.slot {pre.slot}")

    pre_by_addr = {r.reserve_address: r for r in pre.reserves}
    reserve_diffs: list[ReserveDiff] = []
    for r_post in post.reserves:
        r_pre = pre_by_addr.get(r_post.reserve_address)
        if r_pre is None:
            # New reserve added between snapshots — model as zero pre-state
            reserve_diffs.append(
                ReserveDiff(
                    reserve_address=r_post.reserve_address,
                    symbol=r_post.symbol,
                    pre_supply=0,
                    post_supply=r_post.total_supply,
                    pre_borrow=0,
                    post_borrow=r_post.borrowed_amount,
                    pre_util=0.0,
                    post_util=r_post.utilization,
                    pre_price_usd=0.0,
                    post_price_usd=r_post.price_usd,
                )
            )
        else:
            reserve_diffs.append(
                ReserveDiff(
                    reserve_address=r_post.reserve_address,
                    symbol=r_post.symbol,
                    pre_supply=r_pre.total_supply,
                    post_supply=r_post.total_supply,
                    pre_borrow=r_pre.borrowed_amount,
                    post_borrow=r_post.borrowed_amount,
                    pre_util=r_pre.utilization,
                    post_util=r_post.utilization,
                    pre_price_usd=r_pre.price_usd,
                    post_price_usd=r_post.price_usd,
                )
            )

    return MarketDiff(
        market_address=post.market_address,
        pre_slot=pre.slot,
        post_slot=post.slot,
        pre_timestamp=pre.timestamp,
        post_timestamp=post.timestamp,
        reserves=reserve_diffs,
        total_supply_usd_pre=pre.total_supply_usd,
        total_supply_usd_post=post.total_supply_usd,
        total_borrowed_usd_pre=pre.total_borrowed_usd,
        total_borrowed_usd_post=post.total_borrowed_usd,
    )


def summarize_diff(diff: MarketDiff) -> dict[str, float]:
    """One-line summary suitable for embedding in a markdown report."""
    return {
        "slot_delta": float(diff.slot_delta),
        "supply_delta_usd": diff.supply_delta_usd,
        "borrow_delta_usd": diff.borrow_delta_usd,
        "n_reserves": float(len(diff.reserves)),
        "max_abs_util_delta": max(
            (abs(rd.util_delta) for rd in diff.reserves), default=0.0
        ),
        "max_abs_price_delta_pct": max(
            (abs(rd.price_delta_pct) for rd in diff.reserves), default=0.0
        ),
    }
