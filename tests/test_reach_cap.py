"""Under rules 2 a settlement's influence stops growing at a town's (Phase 5 S6)."""

from __future__ import annotations

from parity import SCENARIOS, initial

from sovereign_world import engine
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.rules import rules_for
from sovereign_world.territory import (
    LOSE_THRESHOLD,
    REACH_CAP,
    Settlement,
    Territory,
    advance_territory,
    settlement_strength,
)

RED = EntityId("civilization:0000000001")


def _plain(width: int, height: int) -> WorldMap:
    tiles = tuple(
        Tile(HexCoord(q, r), Terrain.GRASSLAND, 500, 500, 500, 500, 0, 0, 0)
        for r in range(height)
        for q in range(width)
    )
    return WorldMap(width=width, height=height, tiles=tiles)


def _reach(cap: int | None, residents: int) -> int:
    """How many tiles out a lone capital holds after its hold has settled."""
    world = _plain(80, 3)
    city = Settlement(
        settlement_id=EntityId("settlement:1-1"),
        civilization_id=RED,
        tile=HexCoord(0, 1),
        founded_day=0,
        capital=True,
    )
    territory = Territory()
    for day in range(200):
        territory = advance_territory(
            territory, world, [city], {city.settlement_id: residents}, day, reach_cap=cap
        ).territory
    return max(item.tile.q for item in territory.held if item.value >= LOSE_THRESHOLD)


def test_the_cap_is_a_towns_strength_and_rules_two_only() -> None:
    assert settlement_strength(1_000) < REACH_CAP < settlement_strength(2_000)
    assert not rules_for(1).reach_cap
    assert rules_for(2).reach_cap


def test_a_great_city_reaches_no_further_than_a_town_under_the_cap() -> None:
    town = _reach(REACH_CAP, 1_000)
    capped = _reach(REACH_CAP, 25_000)
    uncapped = _reach(None, 25_000)
    assert capped == (REACH_CAP - LOSE_THRESHOLD) // 10
    assert town <= capped < uncapped
    assert uncapped == 79, "without the cap it fills the strip"
    # A village is unaffected.
    assert _reach(REACH_CAP, 32) == _reach(None, 32)


def test_the_engine_applies_the_cap_by_rules_version(monkeypatch) -> None:
    seen: list[int | None] = []
    real = engine.advance_territory

    def spy(*args, **kwargs):
        seen.append(kwargs.get("reach_cap"))
        return real(*args, **kwargs)

    monkeypatch.setattr(engine, "advance_territory", spy)
    for scenario in SCENARIOS:
        state = initial(scenario)
        advance_day(state, StableRng(state.config.seed))
    assert seen == [None, REACH_CAP]
