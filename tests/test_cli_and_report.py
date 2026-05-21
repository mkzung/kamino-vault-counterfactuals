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
