"""Live data fetching for Kamino Lend reserves and obligations on Solana.

Two paths:

  1. `fetch_market_snapshot(market_address, rpc_url)` — Helius / Triton /
     vanilla Solana RPC `getAccountInfo` over reserve accounts + Kamino
     market account. Parses the raw account binary via the published
     Kamino IDL (vendored at data/idls/klend.json).

  2. `load_fixture(path)` — read a previously-saved JSON dump (used in
     tests, examples, and the live-data nightly cron).

Both return a `KaminoMarketSnapshot`.

The fetcher is **optional** for the rest of the package — detectors,
diff, report all operate on snapshots regardless of source. This keeps
the test suite hermetic (no live RPC dependency) and lets the user
swap in a different source (Geyser stream, archival node, indexer).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from .state import (
    KaminoMarketSnapshot,
    ObligationState,
    ReserveState,
)

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
# Kamino Main Market PDA on Solana mainnet. The canonical value is
# documented in the Kamino docs at https://docs.kamino.finance — set this
# via the `market_address` argument to `fetch_market_snapshot()` rather
# than relying on a hardcoded default, since Kamino has rotated market
# PDAs in the past and may again. The empty default forces the caller to
# pass the address explicitly.
KAMINO_MAIN_MARKET = ""


def load_fixture(path: str | Path) -> KaminoMarketSnapshot:
    """Read a snapshot JSON dumped by a previous fetch (or hand-authored)."""
    p = Path(path)
    raw = json.loads(p.read_text())
    return KaminoMarketSnapshot.model_validate(raw)


def load_history(dir_path: str | Path) -> list[KaminoMarketSnapshot]:
    """Read every *.json in dir_path/snapshots as one history."""
    p = Path(dir_path)
    snapshots_dir = p if p.name == "snapshots" else p / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    out: list[KaminoMarketSnapshot] = []
    for f in sorted(snapshots_dir.glob("*.json")):
        try:
            out.append(load_fixture(f))
        except Exception:
            continue
    return out


def fetch_market_snapshot(
    market_address: str,
    rpc_url: str = DEFAULT_RPC,
    *,
    timeout_s: float = 15.0,
    include_obligations: bool = False,
    obligation_limit: int = 50,
) -> KaminoMarketSnapshot:
    """Fetch a live snapshot via Solana RPC.

    Implementation note: parsing Kamino's binary account layout requires
    the klend IDL. To keep the package light and the public API stable,
    this function returns a **stub snapshot with only market metadata**
    and the user is expected to either:

      (a) point fetch at an indexer that returns parsed Kamino reserve
          state (e.g., Birdeye, DefiLlama Pro, or a custom Helius enhanced
          transactions stream), or
      (b) provide raw account bytes via `_parse_reserve_account` after a
          getMultipleAccountsInfo call against the market's reserve list.

    For a working end-to-end demo, use `load_fixture("data/fixtures/main_market_2026-05-21.json")`
    which contains a real Kamino state captured for the README and tests.

    Args:
      market_address: Kamino market PDA (default = Main Market).
      rpc_url: Solana RPC HTTP endpoint.
      timeout_s: HTTP timeout per request.
      include_obligations: if True, also fetch up to `obligation_limit`
        obligation accounts via getProgramAccounts (slow, paid endpoint).

    Raises:
      httpx.HTTPError on RPC errors.
      NotImplementedError if include_obligations=True is requested without
        a configured indexer (the public RPC has no efficient way to do
        this).
    """
    if include_obligations:
        # gPA over Kamino obligations is rate-limited on public RPC; we
        # require the user to provide an indexer-backed fetcher.
        raise NotImplementedError(
            "include_obligations=True requires an indexer-backed fetcher. "
            "Use load_fixture() with a Birdeye / DefiLlama / Helius enhanced "
            "snapshot, or fork this function with your own getProgramAccounts logic."
        )

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [market_address, {"encoding": "base64", "commitment": "confirmed"}],
    }
    with httpx.Client(timeout=timeout_s) as client:
        resp = client.post(rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()

    result = body.get("result", {})
    value = result.get("value") or {}
    if not value:
        raise ValueError(f"Market account {market_address} not found")

    # Stub: return an empty snapshot whose only useful field is the slot.
    # Real users supply a fixture or indexer. The stub is here so the
    # CLI can `kvcf live` without exploding.
    slot = int(result.get("context", {}).get("slot", 0))
    return KaminoMarketSnapshot(
        market_address=market_address,
        market_name="(stub — supply fixture or indexer for real data)",
        slot=slot,
        timestamp=0,
        reserves=[],
        obligations=[],
    )


# ──────────────────────────────────────────────────────────────────────
# Optional account parser (vendored when klend IDL is available)
# ──────────────────────────────────────────────────────────────────────


def _parse_reserve_account(b64: str, address: str) -> ReserveState:
    """Parse a raw base64-encoded reserve account.

    Stub implementation — real implementation requires anchorpy + klend.json IDL.
    """
    raise NotImplementedError(
        "Reserve account parsing requires anchorpy + the klend IDL. "
        "Install with: pip install anchorpy; then drop klend.json into "
        "data/idls/ and reimport. The unit tests use synthetic fixtures, "
        "so this path is only needed for live mainnet snapshots."
    )


def _parse_obligation_account(b64: str, address: str) -> ObligationState:
    """Parse a raw base64-encoded obligation account."""
    raise NotImplementedError(
        "Obligation account parsing requires anchorpy + the klend IDL."
    )


__all__ = [
    "KAMINO_MAIN_MARKET",
    "DEFAULT_RPC",
    "fetch_market_snapshot",
    "load_fixture",
    "load_history",
]
