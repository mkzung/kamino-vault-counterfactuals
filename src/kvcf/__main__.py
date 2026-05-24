"""CLI entry point: `kvcf <command>`.

Commands:
  demo                          — run all detectors on a synthetic snapshot, print markdown.
  demo --json                   — same, but JSON output.
  demo --html OUT.html          — write HTML report to OUT.html.
  run FIXTURE.json              — run all detectors on a loaded snapshot fixture.
  diff PRE.json POST.json       — print snapshot-to-snapshot diff.
  replay --fixtures-dir DIR ... — iterate every *.json fixture in DIR (sorted by
                                  slot) and emit one chronological JSON report
                                  with all 6 detector results per snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diff import diff_snapshots
from .fetch import load_fixture
from .html_report import as_html
from .report import as_json, as_markdown
from .runner import RunnerConfig, run_all_detectors
from .synthetic import make_market_snapshot


def _cmd_demo(args: argparse.Namespace) -> int:
    snap = make_market_snapshot(
        seed=args.seed, n_healthy=args.healthy, n_at_risk=args.at_risk, n_underwater=args.underwater
    )
    results = run_all_detectors(snap)
    # Unified precedence (matches `run`): --json wins if both passed —
    # JSON is the more compositable format for downstream tooling.
    if args.json:
        print(as_json(results))
        return 0
    if args.html:
        Path(args.html).write_text(as_html(results, title="Kamino Vault Counterfactuals — Demo"))
        print(f"Wrote HTML report → {args.html}", file=sys.stderr)
        return 0
    print(as_markdown(results, title="Kamino Vault Counterfactuals — Demo Report"))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    snap = load_fixture(args.fixture)
    cfg = RunnerConfig(
        drift_pct=args.drift,
        shock_pct=args.shock,
        util_target_max=args.util_target,
        near_pp=args.near_pp,
    )
    results = run_all_detectors(snap, cfg)
    # Unified precedence (matches `demo`): --json wins if both passed.
    if args.json:
        print(as_json(results))
    elif args.html:
        Path(args.html).write_text(as_html(results, title=f"Kamino Report — slot {snap.slot}"))
        print(f"Wrote HTML report → {args.html}", file=sys.stderr)
    else:
        print(as_markdown(results, title=f"Kamino Report — slot {snap.slot}"))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Iterate every *.json fixture in a directory and produce one
    chronological JSON report. Output schema:

        {
          "fixtures_dir": "...",
          "n_snapshots": int,
          "snapshots": [
            {
              "fixture": "main_market_2026-05-21.json",
              "slot": int,
              "timestamp": int,
              "market_address": str,
              "results": [<DetectorResult-as-dict>, ...]
            },
            ...
          ]
        }

    Snapshots are ordered by slot ascending (the natural "replay" order).
    Use `--out` to write to a file; otherwise prints to stdout.
    """
    from dataclasses import asdict

    from .report import _sanitize

    fixtures_dir = Path(args.fixtures_dir)
    if not fixtures_dir.is_dir():
        print(f"error: {fixtures_dir} is not a directory", file=sys.stderr)
        return 2
    files = sorted(fixtures_dir.glob("*.json"))
    if not files:
        print(f"error: no *.json fixtures in {fixtures_dir}", file=sys.stderr)
        return 2

    cfg = RunnerConfig(
        drift_pct=args.drift,
        shock_pct=args.shock,
        util_target_max=args.util_target,
        near_pp=args.near_pp,
    )

    from .state import KaminoMarketSnapshot

    loaded: list[tuple[Path, KaminoMarketSnapshot]] = []
    for f in files:
        snap = load_fixture(f)
        loaded.append((f, snap))
    # Chronological by slot
    loaded.sort(key=lambda t: t[1].slot)

    out_snapshots = []
    for fpath, snap in loaded:
        results = run_all_detectors(snap, cfg)
        out_snapshots.append(
            {
                "fixture": fpath.name,
                "slot": snap.slot,
                "timestamp": snap.timestamp,
                "market_address": snap.market_address,
                "results": _sanitize([asdict(r) for r in results]),
            }
        )

    report = {
        "fixtures_dir": str(fixtures_dir),
        "n_snapshots": len(out_snapshots),
        "snapshots": out_snapshots,
    }
    payload = json.dumps(report, indent=2, allow_nan=False)
    if args.out:
        Path(args.out).write_text(payload)
        print(f"Wrote replay report → {args.out} ({len(out_snapshots)} snapshots)", file=sys.stderr)
    else:
        print(payload)
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    pre = load_fixture(args.pre)
    post = load_fixture(args.post)
    md = diff_snapshots(pre, post)
    print(f"Market: {md.market_address}")
    print(f"Slot delta: {md.slot_delta} ({md.pre_slot} → {md.post_slot})")
    print(f"Total supply USD: {md.total_supply_usd_pre:,.0f} → {md.total_supply_usd_post:,.0f} "
          f"(Δ {md.supply_delta_usd:+,.0f})")
    print(f"Total borrow USD: {md.total_borrowed_usd_pre:,.0f} → {md.total_borrowed_usd_post:,.0f} "
          f"(Δ {md.borrow_delta_usd:+,.0f})")
    print("Reserves:")
    for rd in md.reserves:
        print(
            f"  {rd.symbol:8s} util {rd.pre_util:.1%} → {rd.post_util:.1%} "
            f"(Δ {rd.util_delta:+.1%}) | price {rd.pre_price_usd:8.2f} → "
            f"{rd.post_price_usd:8.2f} ({rd.price_delta_pct:+.2%})"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kvcf", description="Kamino Vault Counterfactuals CLI")
    sub = p.add_subparsers(dest="command", required=True)

    # demo
    p_demo = sub.add_parser("demo", help="Run detectors on a synthetic snapshot.")
    p_demo.add_argument("--seed", type=int, default=0)
    p_demo.add_argument("--healthy", type=int, default=5)
    p_demo.add_argument("--at-risk", type=int, default=2)
    p_demo.add_argument("--underwater", type=int, default=0)
    p_demo.add_argument("--json", action="store_true", help="Output JSON.")
    p_demo.add_argument("--html", type=str, default=None, help="Write HTML to file.")
    p_demo.set_defaults(func=_cmd_demo)

    # run
    p_run = sub.add_parser("run", help="Run detectors on a fixture file.")
    p_run.add_argument("fixture", type=str)
    p_run.add_argument("--drift", type=float, default=-0.10)
    p_run.add_argument("--shock", type=float, default=-0.20)
    p_run.add_argument("--util-target", type=float, default=0.90)
    p_run.add_argument("--near-pp", type=float, default=5.0)
    p_run.add_argument("--json", action="store_true")
    p_run.add_argument("--html", type=str, default=None)
    p_run.set_defaults(func=_cmd_run)

    # diff
    p_diff = sub.add_parser("diff", help="Diff two snapshot fixtures.")
    p_diff.add_argument("pre", type=str)
    p_diff.add_argument("post", type=str)
    p_diff.set_defaults(func=_cmd_diff)

    # replay — historical replay over a fixtures dir
    p_replay = sub.add_parser(
        "replay",
        help="Run all detectors on every *.json fixture in a dir, emit chronological JSON.",
    )
    p_replay.add_argument(
        "--fixtures-dir", type=str, required=True,
        help="Directory containing *.json snapshot fixtures.",
    )
    p_replay.add_argument(
        "--out", type=str, default=None,
        help="Write JSON report to this path (default: stdout).",
    )
    p_replay.add_argument("--drift", type=float, default=-0.10)
    p_replay.add_argument("--shock", type=float, default=-0.20)
    p_replay.add_argument("--util-target", type=float, default=0.90)
    p_replay.add_argument("--near-pp", type=float, default=5.0)
    p_replay.set_defaults(func=_cmd_replay)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
