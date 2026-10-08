"""The sealed trial's record names only tests that exist, and maps the roadmap's exit sentence
clause by clause (sealed trial, slice I).

`docs/sealed-trial-record.md` maps each clause of Phase 5's exit condition to the tests that
prove it on stand-ins. A test renamed or removed would leave a clause unproved without anyone
noticing; this keeps the mapping honest, as the observer and launch-gate checklists are kept.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "docs" / "sealed-trial-record.md"
ROADMAP = ROOT / "docs" / "superpowers" / "plans" / "2026-09-21-ai-civilization-world-roadmap.md"
REPORT = ROOT / "docs" / "calibration" / "2026-10-trial-3civ" / "report.json"
CLAUSES = (
    "the signed manifest is immutable",
    "all launch-gate checks pass",
    "the live world can recover from the last checkpoint without divergent replay",
)


def _python_tests() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        names.update(re.findall(r"^def (test_[a-z0-9_]+)\(", path.read_text(), re.MULTILINE))
    return names


def test_every_test_the_record_names_exists() -> None:
    named = re.findall(r"`(test_[a-z0-9_]+)`", RECORD.read_text())
    assert len(set(named)) >= 20
    missing = sorted(set(named) - _python_tests())
    assert missing == [], f"the record names tests that do not exist: {missing}"


def test_each_exit_clause_has_its_row() -> None:
    roadmap = " ".join(ROADMAP.read_text().split())
    rows = [line for line in RECORD.read_text().splitlines() if line.startswith("| ")]
    for clause in CLAUSES:
        assert clause in roadmap, clause
        assert sum(row.startswith(f"| {clause} |") for row in rows) == 1, clause


def test_the_record_quotes_the_balance_reports_engine() -> None:
    engine = json.loads(REPORT.read_text())["engine_hash"]
    assert f"`{engine}`" in RECORD.read_text()


def test_the_documents_it_points_to_exist() -> None:
    for target in re.findall(r"\]\(([^)#]+\.md)\)", RECORD.read_text()):
        assert (RECORD.parent / target).exists(), target
