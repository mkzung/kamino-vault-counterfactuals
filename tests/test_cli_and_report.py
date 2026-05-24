"""Tests for CLI, JSON/Markdown/HTML reporting, and runner orchestration."""

from __future__ import annotations

import json

from kvcf.__main__ import main
from kvcf.html_report import as_html
from kvcf.report import as_json, as_markdown
from kvcf.runner import RunnerConfig, run_all_detectors, summarize
from kvcf.synthetic import make_market_snapshot


def test_run_all_detectors_returns_six():
    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    assert len(results) == 6
    names = [r.name for r in results]
    assert names == [
        "OracleStalenessReplay",
        "CollateralCascade",
        "DepositorExitShock",
        "UtilizationBandBreach",
        "LiquidationLatency",
        "LTVDistributionStress",
    ]


def test_runner_config_is_used():
    snap = make_market_snapshot(n_healthy=0, n_at_risk=3, n_underwater=0)
    weak = RunnerConfig(drift_pct=-0.02, shock_pct=-0.02)
    strong = RunnerConfig(drift_pct=-0.40, shock_pct=-0.40)
    weak_res = run_all_detectors(snap, weak)
    strong_res = run_all_detectors(snap, strong)
    # Stronger shock should produce equal-or-larger cascade metric
    weak_cascade = next(r for r in weak_res if r.name == "CollateralCascade")
    strong_cascade = next(r for r in strong_res if r.name == "CollateralCascade")
    assert strong_cascade.headline_metric >= weak_cascade.headline_metric


def test_summarize_returns_one_per_detector():
    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    s = summarize(results)
    assert len(s) == 6
    assert all(isinstance(v, float) for v in s.values())


def test_as_json_round_trips():
    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    j = as_json(results)
    parsed = json.loads(j)
    assert isinstance(parsed, list)
    assert len(parsed) == 6
    assert parsed[0]["name"] == "OracleStalenessReplay"


def test_as_markdown_contains_all_detector_headings():
    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    md = as_markdown(results)
    for name in [
        "OracleStalenessReplay",
        "CollateralCascade",
        "DepositorExitShock",
        "UtilizationBandBreach",
        "LiquidationLatency",
        "LTVDistributionStress",
    ]:
        assert f"## {name}" in md


def test_as_html_is_self_contained():
    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    h = as_html(results)
    assert h.startswith("<!DOCTYPE html>")
    assert "</html>" in h
    # Must include all detector names
    for name in [
        "OracleStalenessReplay",
        "CollateralCascade",
        "DepositorExitShock",
        "UtilizationBandBreach",
        "LiquidationLatency",
        "LTVDistributionStress",
    ]:
        assert name in h


def test_html_severity_classes_used_appropriately():
    snap = make_market_snapshot(n_healthy=0, n_at_risk=10, n_underwater=5)
    results = run_all_detectors(snap, RunnerConfig(drift_pct=-0.40))
    h = as_html(results)
    # At least one severe metric → expect either warn or danger class present
    assert ("class=\"headline warn\"" in h) or ("class=\"headline danger\"" in h)


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────


def test_cli_demo_markdown(capsys):
    rc = main(["demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "OracleStalenessReplay" in out
    assert "CollateralCascade" in out


def test_cli_demo_json(capsys):
    rc = main(["demo", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    parsed = json.loads(out)
    assert len(parsed) == 6


def test_cli_demo_html(tmp_path):
    out_path = tmp_path / "report.html"
    rc = main(["demo", "--html", str(out_path)])
    assert rc == 0
    content = out_path.read_text()
    assert "<!DOCTYPE html>" in content
    assert "OracleStalenessReplay" in content


def test_cli_run_loads_fixture(tmp_path):
    # Dump a synthetic snapshot, load via CLI
    snap = make_market_snapshot()
    fp = tmp_path / "fixture.json"
    fp.write_text(snap.model_dump_json())
    rc = main(["run", str(fp)])
    assert rc == 0


def test_cli_diff(tmp_path, capsys):
    pre = make_market_snapshot(slot=100)
    post = make_market_snapshot(slot=200)
    pre_f = tmp_path / "pre.json"
    post_f = tmp_path / "post.json"
    pre_f.write_text(pre.model_dump_json())
    post_f.write_text(post.model_dump_json())
    rc = main(["diff", str(pre_f), str(post_f)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Slot delta: 100" in out
    assert "Reserves:" in out


# ──────────────────────────────────────────────────────────────────────
# Round-4 regression tests: as_json must be strict-parseable; detectors
# must not raise KeyError on missing reserves.
# ──────────────────────────────────────────────────────────────────────


def test_as_json_emits_strict_parseable_when_metric_is_inf():
    """`json.dumps` writes literal `Infinity` by default, which is invalid
    JSON. The Round-4 fix sanitizes ±inf/NaN to string sentinels so any
    standard parser (browsers, jq, Go, Rust) can round-trip the output.
    """
    from kvcf.detectors import DetectorResult

    results = [
        DetectorResult(
            name="DepositorExitShock",
            headline_metric=float("inf"),
            headline_unit="fraction_or_inf",
            interpretation="Reserve fully drained — utilization undefined.",
            evidence={
                "post_util": float("inf"),
                "nan_value": float("nan"),
                "neg_inf_value": float("-inf"),
                "normal_value": 0.5,
            },
        )
    ]
    payload = as_json(results)
    # The string `Infinity` (Python's default) is NOT valid JSON.
    assert "Infinity" not in payload
    assert "NaN" not in payload
    # Standard json.loads must round-trip cleanly:
    parsed = json.loads(payload)
    assert parsed[0]["headline_metric"] == "inf"
    assert parsed[0]["evidence"]["post_util"] == "inf"
    assert parsed[0]["evidence"]["nan_value"] == "nan"
    assert parsed[0]["evidence"]["neg_inf_value"] == "-inf"
    assert parsed[0]["evidence"]["normal_value"] == 0.5


def test_as_html_deterministic_with_now_seam():
    """v0.2.0: as_html accepts a `now` argument for reproducible output."""
    from datetime import datetime, timezone

    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    fixed = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    a = as_html(results, now=fixed)
    b = as_html(results, now=fixed)
    assert a == b
    assert "2026-05-25 12:00 UTC" in a


def test_as_html_honors_source_date_epoch(monkeypatch):
    """v0.2.0: as_html honors SOURCE_DATE_EPOCH for reproducible-builds."""
    snap = make_market_snapshot()
    results = run_all_detectors(snap)
    # 2026-05-25 00:00:00 UTC
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1779667200")
    h = as_html(results)
    assert "2026-05-25 00:00 UTC" in h


# ──────────────────────────────────────────────────────────────────────
# CLI replay (v0.2.0 — historical replay over a fixtures dir)
# ──────────────────────────────────────────────────────────────────────


def test_cli_replay_emits_chronological_report(tmp_path, capsys):
    # Two synthetic snapshots, slot 100 and 200; write to a fixtures dir.
    pre = make_market_snapshot(slot=100)
    post = make_market_snapshot(slot=200)
    fdir = tmp_path / "fix"
    fdir.mkdir()
    (fdir / "a.json").write_text(pre.model_dump_json())
    (fdir / "b.json").write_text(post.model_dump_json())

    out_path = tmp_path / "replay.json"
    rc = main(["replay", "--fixtures-dir", str(fdir), "--out", str(out_path)])
    assert rc == 0
    payload = json.loads(out_path.read_text())
    assert payload["n_snapshots"] == 2
    assert payload["snapshots"][0]["slot"] == 100
    assert payload["snapshots"][1]["slot"] == 200
    # Each snapshot must have all 6 detector results
    for s in payload["snapshots"]:
        assert len(s["results"]) == 6
        names = [r["name"] for r in s["results"]]
        assert names == [
            "OracleStalenessReplay",
            "CollateralCascade",
            "DepositorExitShock",
            "UtilizationBandBreach",
            "LiquidationLatency",
            "LTVDistributionStress",
        ]


def test_cli_replay_runs_against_bundled_fixtures(capsys):
    """Sanity check: shipped fixtures parse + run end-to-end through replay."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    fdir = repo_root / "data" / "fixtures"
    rc = main(["replay", "--fixtures-dir", str(fdir)])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["n_snapshots"] >= 2
    # Slots must be strictly ascending (chronological)
    slots = [s["slot"] for s in payload["snapshots"]]
    assert slots == sorted(slots)


# ──────────────────────────────────────────────────────────────────────
# Bundled fixtures must always parse — protects against schema drift.
# ──────────────────────────────────────────────────────────────────────


def test_all_bundled_fixtures_parse():
    """Every *.json in data/fixtures must load as KaminoMarketSnapshot.

    A failing fixture means either: (a) the schema changed without
    updating the bundled examples, or (b) someone hand-edited a fixture
    into invalid shape. Either way, the README's "load_fixture()"
    examples would silently break for users — CI catches it here.
    """
    import pathlib

    from kvcf.fetch import load_fixture

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    fdir = repo_root / "data" / "fixtures"
    files = sorted(fdir.glob("*.json"))
    assert files, "no bundled fixtures found in data/fixtures/"
    for f in files:
        snap = load_fixture(f)
        assert snap.market_address
        assert snap.slot >= 0


def test_fixtures_have_meaningful_diff():
    """v0.2.0: the two bundled fixtures must show non-trivial deltas so
    the README diff example is not a no-op.
    """
    import pathlib

    from kvcf.diff import diff_snapshots
    from kvcf.fetch import load_fixture

    repo_root = pathlib.Path(__file__).resolve().parents[1]
    fdir = repo_root / "data" / "fixtures"
    pre = load_fixture(fdir / "main_market_2026-05-21.json")
    post = load_fixture(fdir / "main_market_2026-05-22.json")
    md = diff_snapshots(pre, post)
    # At least one reserve must show a non-zero price delta.
    price_deltas = [abs(rd.price_delta_pct) for rd in md.reserves]
    assert max(price_deltas) > 0.0
    # Total supply USD must differ (we cut SOL liquidity + price).
    assert md.supply_delta_usd != 0.0


# ──────────────────────────────────────────────────────────────────────
# Synthetic addresses must be real base58 (v0.2.0 — P1 #14).
# ──────────────────────────────────────────────────────────────────────


def test_synthetic_mint_addresses_are_valid_base58():
    import base58

    snap = make_market_snapshot(n_healthy=2, n_at_risk=1, n_underwater=0)
    for r in snap.reserves:
        # The static SOL_RESERVE / USDC_RESERVE etc. are placeholders
        # that already pass base58 (they use the base58 alphabet). What
        # we MUST verify is the generated mint addresses round-trip
        # cleanly through b58decode.
        base58.b58decode(r.mint)
        assert 40 <= len(r.mint) <= 48
    for ob in snap.obligations:
        base58.b58decode(ob.obligation_address)
        base58.b58decode(ob.owner)
        assert 40 <= len(ob.obligation_address) <= 48
    for _, deps in snap.top_depositors_by_reserve.items():
        for addr, _amt in deps:
            base58.b58decode(addr)


def test_collateral_cascade_skips_missing_reserve_gracefully():
    """An obligation can reference a reserve that's been delisted between
    snapshot dumps. The detector should treat that bucket's available
    liquidity as 0 (worst case) rather than raise KeyError.
    """
    from kvcf.detectors import CollateralCascade
    from kvcf.synthetic import make_market_snapshot

    # Take a synthetic underwater-heavy snapshot, then rewrite one
    # obligation's top-collateral deposit to point at a delisted reserve.
    snap = make_market_snapshot(n_healthy=0, n_at_risk=0, n_underwater=3)
    old_oblig = snap.obligations[0]
    new_deposits = [
        old_oblig.deposits[0].model_copy(
            update={"reserve_address": "MISSING_DELISTED_1111111111"}
        )
    ] + list(old_oblig.deposits[1:])
    new_oblig = old_oblig.model_copy(update={"deposits": new_deposits})
    new_snap = snap.model_copy(
        update={"obligations": [new_oblig] + list(snap.obligations[1:])}
    )

    # Must NOT raise KeyError (pre-Round-4 behavior)
    result = CollateralCascade(shock_pct=-0.5).run(new_snap)
    assert result.name == "CollateralCascade"
    per_reserve = result.evidence.get("per_reserve", {})
    # If the missing reserve was attributed (it should be, given it's the
    # only deposit on that obligation), the bucket must be flagged.
    if "MISSING_DELISTED_1111111111" in per_reserve:
        b = per_reserve["MISSING_DELISTED_1111111111"]
        assert b.get("reserve_missing") == 1.0
        assert b["available_liquidity_usd"] == 0.0
