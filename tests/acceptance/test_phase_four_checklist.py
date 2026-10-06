"""The observer acceptance checklist names only tests that exist (Phase 4 exit, O6).

`docs/observer-acceptance.md` maps each Phase 4 promise to the tests that prove it. A test
renamed or removed would leave a promise unproved without anyone noticing; this keeps the list
honest.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKLIST = ROOT / "docs" / "observer-acceptance.md"
PYTHON_NAME = re.compile(r"`(test_[a-z0-9_]+)`")
BROWSER_NAME = re.compile(r"`([a-z0-9-]+\.test\.mjs)` \u203a \"([^\"]+)\"")


def _python_tests() -> set[str]:
    names: set[str] = set()
    for path in (ROOT / "tests").rglob("*.py"):
        names.update(re.findall(r"^def (test_[a-z0-9_]+)\(", path.read_text(), re.MULTILINE))
    return names


def test_every_test_the_checklist_names_exists() -> None:
    text = CHECKLIST.read_text()
    python = PYTHON_NAME.findall(text)
    browser = BROWSER_NAME.findall(text)
    assert len(python) > 40 and len(browser) > 15
    missing = sorted(set(python) - _python_tests())
    assert missing == [], f"the checklist names Python tests that do not exist: {missing}"
    for file, title in browser:
        path = ROOT / "observer" / "tests" / file
        assert path.exists(), file
        assert title in path.read_text(), f"{file} has no test titled {title!r}"
