"""The fairness report's checks, on rows made by hand (sealed trial)."""

from __future__ import annotations

from sovereign_world.calibration.report import evaluate


def _row(
    assignment: str, position: int, living: int, *, policy: str = "builder", win: float = 0.25
) -> dict[str, str]:
    return {
        "assignment": assignment,
        "seed": "0",
        "rotation": str(position),
        "position": str(position),
        "civilization": str(position),
        "policy": policy,
        "living": str(living),
        "peak": str(living),
        "tiles": "10",
        "settlements": "1",
        "winner_share": str(win),
        "wars_declared": "0",
        "battles": "0",
        "rejected_orders": "0",
    }


def test_even_starts_pass() -> None:
    rows = [_row("builders", position, 40) for position in range(4) for _ in range(5)]
    result = evaluate(rows)
    assert result["passed"] is True
    tables = result["tables"]
    assert isinstance(tables, dict)
    assert tables["builders by start position"]["0"]["living"] == 40


def test_a_favoured_start_fails() -> None:
    rows = [
        _row("builders", position, 60 if position == 0 else 40, win=0.7 if position == 0 else 0.1)
        for position in range(4)
    ]
    result = evaluate(rows)
    assert result["passed"] is False
    checks = result["checks"]
    assert isinstance(checks, list)
    failed = {check["check"] for check in checks if not check["passed"]}
    assert "builders: position 0 mean population within ±15%" in failed
    assert any("win share" in name for name in failed)


def test_a_start_where_people_die_out_fails_survival() -> None:
    rows = [
        _row("builders", position, 0 if position == 2 else 40)
        for position in range(4)
        for _ in range(3)
    ]
    result = evaluate(rows)
    checks = result["checks"]
    assert isinstance(checks, list)
    assert any(
        check["check"] == "builders: position 2 survival at least 95%" and not check["passed"]
        for check in checks
    )


def test_mixed_checks_each_policy_across_starts() -> None:
    rows = [
        _row("mixed", position, 30 if (policy == "raider" and position == 3) else 50, policy=policy)
        for policy in ("builder", "raider")
        for position in range(4)
    ]
    result = evaluate(rows)
    checks = result["checks"]
    assert isinstance(checks, list)
    failed = {check["check"] for check in checks if not check["passed"]}
    assert failed == {"mixed: raider at position 3 within ±20%"}


def test_the_win_share_band_follows_the_number_of_starts() -> None:
    even = [_row("builders", position, 40, win=1 / 3) for position in range(3) for _ in range(5)]
    result = evaluate(even)
    assert result["passed"] is True
    thresholds = result["thresholds"]
    assert isinstance(thresholds, dict) and thresholds["win_share"] == [0.2, 0.4667]
    favoured = [
        _row("builders", position, 40, win=0.5 if position == 0 else 0.25)
        for position in range(3)
        for _ in range(5)
    ]
    checks = evaluate(favoured)["checks"]
    assert isinstance(checks, list)
    failed = {check["check"] for check in checks if not check["passed"]}
    assert failed == {"builders: position 0 win share in 20% to 47%"}
    four = evaluate([_row("builders", position, 40) for position in range(4)])["checks"]
    assert isinstance(four, list)
    assert "builders: position 0 win share in 15% to 35%" in {check["check"] for check in four}
