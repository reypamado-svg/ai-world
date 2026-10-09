"""A short recorded war (a raid on the road, then peace and a ceded colony), for the
observer's tests: the chronicle's and the browser's travellers.

    PYTHONPATH=src:tests .venv/bin/python tests/observer/war_run.py OUT [DAYS]

writes a format-2 run to OUT (which must not exist).
"""

from __future__ import annotations

import sys
from pathlib import Path


def record_war(root: Path, days: int = 150) -> None:
    from test_journal_deltas import _cession_world, _record

    start, step = _cession_world()
    _record(root, start, step, days)


if __name__ == "__main__":
    record_war(Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 150)
