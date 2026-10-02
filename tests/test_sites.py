"""Ore deposits, quarries, ancient ruins and troves: placed fairly, known only where seen."""

from collections import Counter

import pytest
from noninterference import assert_no_hidden_knowledge
from scenario_helpers import Council

from sovereign_world.commands import build_council_report
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.gateway.memory import state_summary
from sovereign_world.hexmap import Terrain
from sovereign_world.rng import StableRng
from sovereign_world.sites import KIT, SITE_SPACING, START_CLEARANCE, SiteKind
from sovereign_world.state import WorldState, build_initial_state, state_hash, validate_world
from sovereign_world.worldgen import GeneratedWorld, generate_world


def _world(seed: int, size: int, count: int = 4) -> GeneratedWorld:
    config = WorldConfig(seed=seed, width=size, height=size, civilizations=count)
    return generate_world(config, StableRng(seed), generator_version=3)


CASES = [
    *((seed, 24, count) for seed in range(6) for count in (2, 3, 4)),
    *((seed, 48, count) for seed in (9, 21) for count in (2, 3, 4)),
    (21, 100, 4),
]


@pytest.mark.parametrize(("seed", "size", "count"), CASES)
def test_every_civilization_gets_the_same_kit_at_similar_distances(
    seed: int, size: int, count: int
) -> None:
    world = _world(seed, size, count)
    centres = [start.center for start in world.starts]
    kit = Counter(kind for kind, _, _ in KIT)
    owned: dict[int, list] = {index: [] for index in range(len(centres))}
    for site in world.sites:
        distances = [site.tile.distance(centre) for centre in centres]
        nearest = min(distances)
        assert distances.count(nearest) == 1, "a site is strictly nearer one start"
        assert nearest > START_CLEARANCE
        assert world.world_map.tile(site.tile).terrain is not Terrain.WATER
        owned[distances.index(nearest)].append((site, nearest))
    for index, sites in owned.items():
        assert Counter(site.kind for site, _ in sites) == kit, f"civilization {index}"
        for kind in set(kit):
            bands = sorted((low, high) for item, low, high in KIT if item is kind)
            distances = sorted(distance for site, distance in sites if site.kind is kind)
            for (low, high), distance in zip(bands, distances, strict=True):
                assert low - 1 <= distance <= high + 1, (kind, distance, (low, high))
    for left in world.sites:
        for right in world.sites:
            if left is not right:
                assert left.tile.distance(right.tile) >= SITE_SPACING
    assert len({site.site_id for site in world.sites}) == len(world.sites)
    assert list(world.sites) == sorted(world.sites, key=lambda site: site.tile)


def test_sites_repeat_for_the_same_seed() -> None:
    assert _world(9, 48).sites == _world(9, 48).sites


def test_ore_and_stone_sites_sit_on_their_ore_and_stone() -> None:
    world = _world(21, 100)
    tiles = [world.world_map.tile(site.tile) for site in world.sites]
    ore = [
        tile.ore
        for site, tile in zip(world.sites, tiles, strict=True)
        if site.kind is SiteKind.ORE_DEPOSIT
    ]
    stone = [
        tile.stone
        for site, tile in zip(world.sites, tiles, strict=True)
        if site.kind is SiteKind.QUARRY
    ]
    land = [tile for tile in world.world_map.tiles if tile.terrain is not Terrain.WATER]
    assert sum(ore) / len(ore) > sum(tile.ore for tile in land) / len(land)
    assert sum(stone) / len(stone) > sum(tile.stone for tile in land) / len(land)


def _state(seed: int = 9, size: int = 48) -> WorldState:
    manifest = RunManifest.new(WorldConfig(seed=seed, width=size, height=size), "test")
    return build_initial_state(manifest)


def test_a_new_world_holds_its_sites_and_round_trips() -> None:
    state = _state()
    assert len(state.sites) == 4 * len(KIT)
    # Day-0 observations are put in order by the first day; validate after it.
    validate_world(advance_day(state, StableRng(state.config.seed)).state)
    again = WorldState.model_validate_json(state.model_dump_json())
    assert again.sites == state.sites
    assert state_hash(again) == state_hash(state)


def test_rulers_see_only_the_sites_on_tiles_they_know() -> None:
    state = _state()
    for civilization_id, civilization in state.civilizations.items():
        report = build_council_report(state, civilization_id)
        known = set(civilization.known_tiles)
        assert {view.tile for view in report.known_sites} == {
            site.tile for site in state.sites if site.tile in known
        }
        assert all(view.as_of_day == 0 for view in report.known_sites)
    councils = [
        Council(
            state=state,
            civilization_id=civilization_id,
            report=build_council_report(state, civilization_id),
        )
        for civilization_id in sorted(state.civilizations)
    ]
    assert assert_no_hidden_knowledge(councils) == len(state.civilizations)


def test_a_site_becomes_known_once_its_tile_is() -> None:
    state = _state()
    civilization_id = sorted(state.civilizations)[0]
    civilization = state.civilizations[civilization_id]
    far = next(site for site in state.sites if site.tile not in set(civilization.known_tiles))
    before = build_council_report(state, civilization_id)
    assert far.site_id not in {view.site_id for view in before.known_sites}

    civilization.known_tiles = tuple(sorted({*civilization.known_tiles, far.tile}))
    after = build_council_report(state, civilization_id)
    assert far.site_id in {view.site_id for view in after.known_sites}


def test_reports_without_sites_read_exactly_as_before() -> None:
    old = RunManifest(
        run_id="6f1f7c32-3f0b-4f1e-9b55-2a2f1d1c0e14",
        engine_version="0.1.0",
        config=WorldConfig(seed=9, width=48, height=48),
        generator_version=2,
    )
    state = build_initial_state(old)
    civilization_id = sorted(state.civilizations)[0]
    report = build_council_report(state, civilization_id)
    assert report.known_sites == ()
    assert "known_sites" not in report.model_dump(mode="json")
    assert "known_sites" not in state_summary(report, budget=1_000_000)

    new_state = _state()
    new_report = build_council_report(new_state, civilization_id)
    assert new_report.known_sites
    assert "known_sites" in state_summary(new_report, budget=1_000_000)
