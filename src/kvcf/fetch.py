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
import logging
import os
import time
from pathlib import Path

import httpx

from .state import (
    KaminoMarketSnapshot,
    ObligationState,
    ReserveState,
)

logger = logging.getLogger(__name__)

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
# Kamino Main Market PDA on Solana mainnet.
#
# Intentionally `None` — there is NO hardcoded canonical value. Callers
# must pass the market PDA explicitly, either by:
#   - setting the `KAMINO_MAIN_MARKET` environment variable, OR
#   - passing `market_address=` to `fetch_market_snapshot()`.
#
# Rationale: Kamino has rotated market PDAs in the past, and we will not
# ship a "default" that risks silently pointing at a stale or wrong
# market. Look up the current Main Market PDA from Kamino docs
# (https://docs.kamino.finance) at fetch time.
KAMINO_MAIN_MARKET: str | None = os.environ.get("KAMINO_MAIN_MARKET") or None


def load_fixture(path: str | Path) -> KaminoMarketSnapshot:
    """Read a snapshot JSON dumped by a previous fetch (or hand-authored)."""
    p = Path(path)
    raw = json.loads(p.read_text())
    return KaminoMarketSnapshot.model_validate(raw)


def _rpc_post_with_retry(
    rpc_url: str,
    payload: dict[str, object],
    *,
    timeout_s: float = 15.0,
    max_attempts: int = 4,
    backoff_initial_s: float = 1.0,
) -> dict[str, object]:
    """POST a JSON-RPC payload with exponential backoff on 429 / 5xx.

    Public Solana RPC nodes (api.mainnet-beta.solana.com) rate-limit
    aggressively. Helius / Triton paid endpoints occasionally 5xx during
    cluster restarts. Backoff: 1s, 2s, 4s — total worst-case ~7s before
    raising. Network errors (httpx.RequestError) also retry.
    """
    last_exc: Exception | None = None
    backoff = backoff_initial_s
    for attempt in range(max_attempts):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                resp = client.post(rpc_url, json=payload)
            retryable = resp.status_code == 429 or 500 <= resp.status_code < 600
            if retryable and attempt < max_attempts - 1:
                logger.warning(
                    "RPC %s returned %d (attempt %d/%d); retrying in %.1fs",
                    rpc_url, resp.status_code, attempt + 1, max_attempts, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()
            data: dict[str, object] = resp.json()
            return data
        except httpx.RequestError as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                logger.warning(
                    "RPC %s request error %r (attempt %d/%d); retrying in %.1fs",
                    rpc_url, exc, attempt + 1, max_attempts, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    # Shouldn't reach here, but be explicit.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"RPC {rpc_url} failed after {max_attempts} attempts")


def load_history(dir_path: str | Path) -> list[KaminoMarketSnapshot]:
    """Read every *.json in dir_path/snapshots as one history.

    Bad fixtures are logged at WARNING and skipped rather than aborting
    the whole load — a single corrupt file shouldn't take down a
    multi-snapshot replay.
    """
    p = Path(dir_path)
    snapshots_dir = p if p.name == "snapshots" else p / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    out: list[KaminoMarketSnapshot] = []
    for f in sorted(snapshots_dir.glob("*.json")):
        try:
            out.append(load_fixture(f))
        except Exception as exc:
            logger.warning("load_history: skipping %s — %s", f, exc)
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
    which ships a SYNTHETIC Kamino-shape snapshot (see CHANGELOG §0.1.1 —
    real mainnet PDAs were intentionally replaced with `Synthetic*` placeholders).

    Args:
      market_address: Kamino market PDA (required positional — no default;
        the prior baked-in mainnet address was removed in v0.1.1 to prevent
        accidental real-market hits during demos).
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
    body = _rpc_post_with_retry(rpc_url, payload, timeout_s=timeout_s)

    # Surface JSON-RPC errors with full diagnostic — silent swallowing of
    # `body["error"]` leads to confusing downstream KeyErrors.
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        raise RuntimeError(
            f"Solana RPC error from {rpc_url}: "
            f"code={err.get('code')} message={err.get('message')!r} data={err.get('data')!r}"
        )

    raw_result = body.get("result", {}) if isinstance(body, dict) else {}
    result: dict[str, object] = raw_result if isinstance(raw_result, dict) else {}
    raw_value = result.get("value") or {}
    value: dict[str, object] = raw_value if isinstance(raw_value, dict) else {}
    if not value:
        raise ValueError(f"Market account {market_address} not found")

    # Stub: return an empty snapshot whose only useful field is the slot.
    # Real users supply a fixture or indexer. The stub is here so the
    # CLI can `kvcf live` without exploding.
    ctx = result.get("context", {})
    slot = int(ctx.get("slot", 0)) if isinstance(ctx, dict) else 0
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
    "DEFAULT_RPC",
    "fetch_market_snapshot",
    "load_fixture",
    "load_history",
]
