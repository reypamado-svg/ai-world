"""Ancient ruins and troves (rules version 2): found once, by the first party to reach them;
and the goods recipes: metal from ore, tools, planks."""

from logistics_helpers import OneShotSovereign, envelope

from sovereign_world.capabilities import CapabilityId
from sovereign_world.commands import DirectOrder, DirectOrderKind, validate_envelope
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.engine import TransitionResult, advance_day
from sovereign_world.hexmap import Terrain
from sovereign_world.ids import EntityId
from sovereign_world.research import CIVIL_TOPICS
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.sites import FINDS, RUIN_LORE, Site, SiteKind
from sovereign_world.state import WorldState, build_initial_state, validate_world
from sovereign_world.travel import way_to

CONFIG = WorldConfig(seed=9, width=24, height=24)


def _state(rules_version: int = 2) -> WorldState:
    return build_initial_state(RunManifest.new(CONFIG, "0.1.0", rules_version=rules_version))


def _run(
    state: WorldState, days: int, sovereigns=None
) -> tuple[WorldState, list[TransitionResult]]:
    rng = StableRng(state.config.seed)
    results = []
    for _ in range(days):
        result = advance_day(state, rng, sovereigns=sovereigns)
        state = result.state
        results.append(result)
    return state, results


def _events(results, kind: str):
    return [event for result in results for event in result.events.events if event.kind == kind]


def _nearby(state: WorldState, home, kind: SiteKind) -> Site:
    """Move the civilization's own site of this kind to a known tile three tiles away."""
    civilization = state.civilizations[home]
    taken = {site.tile for site in state.sites}
    tile = next(
        coord
        for coord in sorted(civilization.known_tiles)
        if coord.distance(civilization.start_center) == 3
        and coord not in taken
        and state.world_map.tile(coord).terrain not in {Terrain.WATER, Terrain.MOUNTAIN}
        and way_to(state.world_map, civilization.start_center, frozenset({coord})) is not None
    )
    own = min(
        (site for site in state.sites if site.kind is kind),
        key=lambda site: site.tile.distance(civilization.start_center),
    )
    moved = own.model_copy(update={"tile": tile})
    state.sites = tuple(
        sorted(
            (moved if site.site_id == own.site_id else site for site in state.sites),
            key=lambda site: site.tile,
        )
    )
    return moved


def _salvage(state: WorldState, home, site: Site, journey: str = "journey:find") -> DirectOrder:
    route = way_to(state.world_map, state.civilizations[home].start_center, frozenset({site.tile}))
    assert route is not None
    return DirectOrder(
        command_id=journey,
        kind=DirectOrderKind.SALVAGE,
        journey_id=EntityId(journey),
        traveller_ids=state.civilizations[home].population.living_ids[:4],
        route=route,
    )


def _codes(state: WorldState, home, order) -> list[str]:
    return [e.code for e in validate_envelope(envelope(state, home, order), state).errors]


def test_the_first_party_at_a_trove_takes_it_all() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    trove = _nearby(state, home, SiteKind.TROVE)
    order = _salvage(state, home, trove)
    assert _codes(state, home, order) == []
    metal = state.civilizations[home].inventory.quantities.get(Resource.METAL, 0)
    state, results = _run(state, 20, {home: OneShotSovereign(order)})
    [found] = _events(results, "trove_found")
    assert found.payload["units"] == sum(FINDS[SiteKind.TROVE].values())
    assert state.civilizations[home].inventory.quantities[Resource.METAL] == metal + 60
    assert state.civilizations[home].inventory.quantities[Resource.BRONZE_ARMS] == 4
    [site] = [item for item in state.sites if item.site_id == trove.site_id]
    assert site.remaining == 0 and site.spent_day is not None
    validate_world(state)

    # Nothing is left: no party is sent there again.
    assert _codes(state, home, _salvage(state, home, trove, "journey:again")) == [
        "invalid_destination"
    ]


def test_an_ancient_ruin_gives_stone_tools_and_lore() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    ruin = _nearby(state, home, SiteKind.ANCIENT_RUIN)
    state, results = _run(state, 20, {home: OneShotSovereign(_salvage(state, home, ruin))})
    [explored] = _events(results, "ruin_explored")
    first = next(iter(CIVIL_TOPICS))
    assert explored.payload == {
        "units": sum(FINDS[SiteKind.ANCIENT_RUIN].values()),
        "topic": first.value,
        "points": RUIN_LORE,
    }
    assert state.civilizations[home].research_points[first] == RUIN_LORE
    assert state.civilizations[home].inventory.quantities[Resource.TOOL] == 8


def test_a_second_party_arriving_later_finds_nothing() -> None:
    state = _state()
    home = sorted(state.civilizations)[0]
    trove = _nearby(state, home, SiteKind.TROVE)
    order = _salvage(state, home, trove)
    state, _ = _run(state, 1, {home: OneShotSovereign(order)})
    # Another people empties it before the party gets there.
    state.sites = tuple(
        item.model_copy(update={"remaining": 0}) if item.site_id == trove.site_id else item
        for item in state.sites
    )
    state, results = _run(state, 20)
    assert _events(results, "trove_found") == []
    assert len(_events(results, "site_empty")) == 1


def test_older_worlds_salvage_only_fallen_settlements() -> None:
    state = _state(1)
    home = sorted(state.civilizations)[0]
    trove = _nearby(state, home, SiteKind.TROVE)
    assert _codes(state, home, _salvage(state, home, trove)) == ["invalid_destination"]


def _craft(state: WorldState, home, item: Resource, quantity: int = 2) -> DirectOrder:
    return DirectOrder(
        command_id=f"craft:{item.value}",
        kind=DirectOrderKind.CRAFT_EQUIPMENT,
        worker_ids=state.civilizations[home].population.living_ids[:1],
        craft_item=item,
        craft_quantity=quantity,
    )


def test_goods_recipes_under_rules_two() -> None:
    from test_stores import _grant

    state = _state()
    home = sorted(state.civilizations)[0]
    civilization = state.civilizations[home]
    maker = civilization.population.living_ids[0]
    quantities = {**civilization.inventory.quantities, Resource.ORE: 10, Resource.METAL: 4}
    civilization.inventory = civilization.inventory.model_copy(update={"quantities": quantities})
    known = {record.capability for record in civilization.capabilities}
    if CapabilityId.METALLURGY_AWARENESS not in known:
        assert _codes(state, home, _craft(state, home, Resource.METAL)) == ["unqualified_worker"]
        _grant(state, home, maker, CapabilityId.METALLURGY_AWARENESS)
    assert _codes(state, home, _craft(state, home, Resource.METAL)) == []
    assert _codes(state, home, _craft(state, home, Resource.TOOL)) == []
    state, results = _run(state, 5, {home: OneShotSovereign(_craft(state, home, Resource.TOOL))})
    assert _events(results, "equipment_crafted")
    assert state.civilizations[home].inventory.quantities[Resource.TOOL] == 2

    old = _state(1)
    old_home = sorted(old.civilizations)[0]
    assert _codes(old, old_home, _craft(old, old_home, Resource.TOOL)) == ["invalid_craft"]
