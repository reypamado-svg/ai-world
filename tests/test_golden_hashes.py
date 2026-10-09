"""Old runs must replay to exactly the same state, day by day."""

import json

import pytest
from golden import FIXTURE, GOLDEN_RUNS, GoldenRun, daily_hashes

EXPECTED: dict[str, list[str]] = json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("run", GOLDEN_RUNS, ids=lambda run: run.name)
def test_reference_run_keeps_every_daily_hash(run: GoldenRun) -> None:
    expected = EXPECTED[run.name]
    for day, actual in enumerate(daily_hashes(run)):
        assert actual == expected[day], f"{run.name} differs from its recorded hash on day {day}"
    assert day == len(expected) - 1
