"""The launch gate's doc is the code's checklist (sealed trial, slice E): every check the gate
makes is in `docs/launch-gate.md` and back, and every test the doc names exists."""

from __future__ import annotations

import re
from pathlib import Path

from sovereign_world.preflight import CHECKS, STATUSES

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "launch-gate.md"
ROW = re.compile(r"^\| `([a-z_]+)` \| ([^|]+) \|", re.MULTILINE)
PYTHON_NAME = re.compile(r"`(test_[a-z0-9_]+)`")


def _python_tests() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        names.update(re.findall(r"^def (test_[a-z0-9_]+)\(", path.read_text(), re.MULTILINE))
    return names


def test_every_check_in_the_code_is_in_the_doc_and_back() -> None:
    rows = {check_id: title.strip() for check_id, title in ROW.findall(DOC.read_text())}
    assert list(rows) == [check_id for check_id, _ in CHECKS]
    assert rows == dict(CHECKS)


def test_every_test_the_launch_gate_names_exists() -> None:
    named = PYTHON_NAME.findall(DOC.read_text())
    assert len(named) > 20
    missing = sorted(set(named) - _python_tests())
    assert missing == [], f"the launch gate names tests that do not exist: {missing}"


def test_the_statuses_the_doc_uses_are_the_codes() -> None:
    text = DOC.read_text()
    for status in STATUSES:
        assert f"`{status}`" in text
    header = next(line for line in text.splitlines() if line.startswith("| Check |"))
    assert [cell.strip() for cell in header.strip("|").split("|")][2:5] == ["PASS", "WARN", "FAIL"]
