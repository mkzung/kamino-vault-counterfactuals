"""Report rendering — JSON, Markdown.

Given the list of DetectorResults from `runner.run_all_detectors()`,
produce a curator-readable output.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .detectors import DetectorResult


def as_json(results: list[DetectorResult], *, indent: int = 2) -> str:
    """Render the detector results as a JSON string."""
    out = [asdict(r) for r in results]
    return json.dumps(out, indent=indent, default=_json_default)


def _json_default(o: Any) -> Any:
    """Fallback for non-serializable objects (e.g., dict keys that are ints)."""
    if isinstance(o, float) and o == float("inf"):
        return "inf"
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
        lines.append(f"- **Headline:** `{r.headline_metric:.4f}` ({r.headline_unit})")
        lines.append(f"- **Interpretation:** {r.interpretation}")
        lines.append("")
        lines.append("**Evidence:**")
        for k, v in r.evidence.items():
            if isinstance(v, (int, float)):
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
