"""The fairness report of a calibration batch (sealed trial).

It asks whether a start, rather than the play, decides how a civilization fares:

- **builders** (everyone plays the same): each start position's mean final population within
  ±15% of the mean of all, at least 95% of civilizations alive at the end, and each position's
  share of wins between 0.6 and 1.4 times the even share (15% to 35% with four civilizations,
  20% to 47% with three);
- **mixed** (one of each policy, rotated round the starts): for each policy, its mean final
  population at every position within ±20% of that policy's mean.

It also lists results by civilization number (who acts first), by policy, and by policy at
each position.
Writes ``report.md``, ``summary.csv`` and ``report.json`` (whether it passed, each check, the
thresholds and the rule hash the histories were played under).
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import fmean

POSITION_SPREAD = 0.15
SURVIVAL = 0.95
WIN_SHARE = (0.6, 1.4)
"""A position's share of wins, as a multiple of the even share (one in the number of starts)."""


def win_share_band(civilizations: int) -> tuple[float, float]:
    """The win shares a start may have with this many civilizations: 15% to 35% with four."""
    low, high = WIN_SHARE
    return low / civilizations, high / civilizations


POLICY_SPREAD = 0.20
COLUMNS = (
    "n",
    "living",
    "peak",
    "tiles",
    "settlements",
    "survival",
    "win_share",
    "wars",
    "battles",
    "rejected",
)
HEADINGS = {
    "n": "Civilizations",
    "living": "Living",
    "peak": "Peak",
    "tiles": "Tiles",
    "settlements": "Settlements",
    "survival": "Survival",
    "win_share": "Win share",
    "wars": "Wars",
    "battles": "Battles",
    "rejected": "Refused orders",
}
FORMATS = {
    "n": "{:.0f}",
    "living": "{:.1f}",
    "peak": "{:.1f}",
    "tiles": "{:.1f}",
    "settlements": "{:.2f}",
    "survival": "{:.0%}",
    "win_share": "{:.0%}",
    "wars": "{:.2f}",
    "battles": "{:.2f}",
    "rejected": "{:.1f}",
}


def _load(directory: Path) -> list[dict[str, str]]:
    with (directory / "histories.csv").open(newline="") as handle:
        return list(csv.DictReader(handle))


def _group(rows: list[dict[str, str]], *keys: str) -> dict[tuple[str, ...], list[dict[str, str]]]:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    return dict(sorted(groups.items()))


def _stats(rows: list[dict[str, str]]) -> dict[str, float]:
    return {
        "n": len(rows),
        "living": fmean(int(row["living"]) for row in rows),
        "peak": fmean(int(row["peak"]) for row in rows),
        "tiles": fmean(int(row["tiles"]) for row in rows),
        "settlements": fmean(int(row["settlements"]) for row in rows),
        "survival": fmean(1.0 if int(row["living"]) > 0 else 0.0 for row in rows),
        "win_share": fmean(float(row["winner_share"]) for row in rows),
        "wars": fmean(int(row["wars_declared"]) for row in rows),
        "battles": fmean(int(row["battles"]) for row in rows),
        "rejected": fmean(int(row["rejected_orders"]) for row in rows),
    }


def evaluate(rows: list[dict[str, str]]) -> dict[str, object]:
    """The checks and the tables, from the histories' rows."""
    checks: list[dict[str, object]] = []
    builders = [row for row in rows if row["assignment"] == "builders"]
    mixed = [row for row in rows if row["assignment"] == "mixed"]
    tables: dict[str, dict[str, dict[str, float]]] = {}
    band = win_share_band(4)
    if builders:
        by_position = {key[0]: _stats(group) for key, group in _group(builders, "position").items()}
        tables["builders by start position"] = by_position
        tables["builders by civilization"] = {
            key[0]: _stats(group) for key, group in _group(builders, "civilization").items()
        }
        mean = _stats(builders)["living"]
        band = win_share_band(len(by_position))
        for position, stats in by_position.items():
            spread = (stats["living"] - mean) / mean if mean else 0.0
            checks.append(
                {
                    "check": f"builders: position {position} mean population"
                    f" within ±{POSITION_SPREAD:.0%}",
                    "value": round(spread, 4),
                    "passed": abs(spread) <= POSITION_SPREAD,
                }
            )
            checks.append(
                {
                    "check": f"builders: position {position} survival at least {SURVIVAL:.0%}",
                    "value": round(stats["survival"], 4),
                    "passed": stats["survival"] >= SURVIVAL,
                }
            )
            low, high = band
            checks.append(
                {
                    "check": f"builders: position {position} win share in {low:.0%} to {high:.0%}",
                    "value": round(stats["win_share"], 4),
                    "passed": low <= stats["win_share"] <= high,
                }
            )
    if mixed:
        tables["mixed by policy"] = {
            key[0]: _stats(group) for key, group in _group(mixed, "policy").items()
        }
        cells = _group(mixed, "policy", "position")
        tables["mixed by policy and position"] = {
            f"{policy} @ {position}": _stats(group) for (policy, position), group in cells.items()
        }
        for policy, group in _group(mixed, "policy").items():
            mean = _stats(group)["living"]
            for (other, position), cell in cells.items():
                if other != policy[0]:
                    continue
                spread = (_stats(cell)["living"] - mean) / mean if mean else 0.0
                checks.append(
                    {
                        "check": f"mixed: {policy[0]} at position {position}"
                        f" within ±{POLICY_SPREAD:.0%}",
                        "value": round(spread, 4),
                        "passed": abs(spread) <= POLICY_SPREAD,
                    }
                )
    return {
        "passed": bool(checks) and all(bool(check["passed"]) for check in checks),
        "checks": checks,
        "tables": tables,
        "thresholds": {
            "position_spread": POSITION_SPREAD,
            "survival": SURVIVAL,
            "win_share": [round(edge, 4) for edge in band],
            "policy_spread": POLICY_SPREAD,
        },
        "rows": len(rows),
        "histories": len({(row["assignment"], row["seed"], row["rotation"]) for row in rows}),
    }


def write_report(directory: Path) -> dict[str, object]:
    """Write ``report.md``, ``summary.csv`` and ``report.json`` beside ``histories.csv``."""
    rows = _load(directory)
    result = evaluate(rows)
    run = (
        json.loads((directory / "run.json").read_text())
        if (directory / "run.json").exists()
        else {}
    )
    result["rule_hash"] = run.get("rule_hash")
    result["engine_hash"] = run.get("engine_hash")
    for key in ("council_interval_days", "size", "days", "civilizations"):
        result[key] = run.get(key)
    result["engine_version"] = run.get("engine_version")
    (directory / "report.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tables = result["tables"]
    assert isinstance(tables, dict)
    with (directory / "summary.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["table", "group", *COLUMNS])
        for name, table in tables.items():
            for group, stats in table.items():
                writer.writerow([name, group, *(round(stats[key], 3) for key in COLUMNS)])
    passed = "Passed" if result["passed"] else "Failed"
    lines = [
        "# Balance calibration report",
        "",
        f"**{passed}.** {result['histories']} histories, {result['rows']} civilizations."
        f" Engine {run.get('engine_version', '?')},"
        f" {run.get('size', '?')} by {run.get('size', '?')} worlds, {run.get('days', '?')} days,"
        f" councils every {run.get('council_interval_days', '?')} days."
        f" Engine hash `{str(run.get('engine_hash', '?'))[:16]}…`,"
        f" rule hash `{str(run.get('rule_hash', '?'))[:16]}…`.",
        "",
        "## Checks",
        "",
        "| Check | Value | Passed |",
        "|---|---|---|",
    ]
    checks = result["checks"]
    assert isinstance(checks, list)
    for check in checks:
        mark = "yes" if check["passed"] else "**no**"
        lines.append(f"| {check['check']} | {check['value']} | {mark} |")
    for name, table in tables.items():
        lines += [
            "",
            f"## {name[0].upper()}{name[1:]}",
            "",
            "| Group | " + " | ".join(HEADINGS[key] for key in COLUMNS) + " |",
            "|---|" + "---|" * len(COLUMNS),
        ]
        for group, stats in table.items():
            cells = [FORMATS[key].format(stats[key]) for key in COLUMNS]
            lines.append(f"| {group} | " + " | ".join(cells) + " |")
    (directory / "report.md").write_text("\n".join(lines) + "\n")
    return result
