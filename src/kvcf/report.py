"""Report rendering — JSON, Markdown.

Given the list of DetectorResults from `runner.run_all_detectors()`,
produce a curator-readable output.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from typing import Any

from .detectors import DetectorResult


def as_json(results: list[DetectorResult], *, indent: int = 2) -> str:
    """Render the detector results as a JSON string.

    Replaces non-finite floats (±inf, NaN) with the strings "inf"/"-inf"/"nan"
    BEFORE serialization. Standard `json.dumps` emits literal `Infinity`/`NaN`
    tokens which are not valid JSON — `JSON.parse` (browsers), `jq`, Go's
    `encoding/json`, and Rust's `serde_json` all reject them. We sanitize
    the whole tree so downstream consumers can always `JSON.parse(...)` the
    output. `_json_default` remains as a safety net for non-float oddities
    (e.g., int dict keys).
    """
    out = _sanitize([asdict(r) for r in results])
    return json.dumps(out, indent=indent, default=_json_default, allow_nan=False)


def _sanitize(value: Any) -> Any:
    """Recursively replace non-finite floats with string sentinels."""
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize(v) for v in value]
    return value


def _json_default(o: Any) -> Any:
    """Fallback for non-serializable objects (e.g., dict keys that are ints)."""
    raise TypeError(f"Object of type {type(o)} is not JSON serializable")


def as_markdown(
    results: list[DetectorResult],
    *,
    title: str = "Kamino Vault Counterfactuals Report",
) -> str:
    """Render as a markdown report (suitable for GitHub PR comments or
    curator review documents).
    """
    lines: list[str] = [f"# {title}", ""]
    for r in results:
        lines.append(f"## {r.name}")
        lines.append("")
        headline = "n/a" if r.headline_metric is None else f"{r.headline_metric:.4f}"
        lines.append(f"- **Headline:** `{headline}` ({r.headline_unit})")
        lines.append(f"- **Interpretation:** {r.interpretation}")
        lines.append("")
        lines.append("**Evidence:**")
        for k, v in r.evidence.items():
            # Exclude bool — `isinstance(True, int)` is True in Python, which
            # silently formats booleans as "1" / "0" in the numeric branches
            # below. Treat bools as their own scalar category.
            if isinstance(v, bool):
                lines.append(f"- `{k}`: `{v}`")
            elif isinstance(v, (int, float)):
                # Format numbers nicely
                if isinstance(v, float):
                    lines.append(f"- `{k}`: {v:.6f}")
                else:
                    lines.append(f"- `{k}`: {v:,}")
            elif isinstance(v, dict) and v:
                lines.append(f"- `{k}`:")
                for sub_k, sub_v in v.items():
                    lines.append(f"  - `{sub_k}`: `{sub_v}`")
            elif isinstance(v, list) and v:
                lines.append(f"- `{k}`: {len(v)} item{'s' if len(v) != 1 else ''}")
                for item in v[:5]:
                    lines.append(f"  - `{item}`")
                if len(v) > 5:
                    lines.append(f"  - …({len(v) - 5} more)")
            else:
                lines.append(f"- `{k}`: `{v}`")
        lines.append("")
    return "\n".join(lines)
