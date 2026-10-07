from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.exploration import Expedition, ExpeditionStatus, advance_expeditions
from sovereign_world.hexmap import HexCoord, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.state import build_initial_state
from sovereign_world.travel import DAY, entry_cost


def _state():
    config = WorldConfig(seed=21, width=48, height=48)
    return build_initial_state(RunManifest.new(config=config, engine_version="0.1.0"))


def _one_day_away(world_map: WorldMap, origin: HexCoord) -> HexCoord:
    """A neighbour reached in one day: grassland with no river on the border."""
    return next(
        tile
        for tile in world_map.neighbors(origin)
        if entry_cost(world_map, tile, origin=origin) == DAY
    )


def test_expedition_discovers_each_travelled_tile_once() -> None:
    state = _state()
    civilization = state.civilizations[sorted(state.civilizations)[0]]
    explorer = civilization.population.living_ids[0]
    origin = civilization.population.people[explorer].location
    destination = _one_day_away(state.world_map, origin)
    expedition = Expedition(
        expedition_id=EntityId("expedition:1"),
        explorer_ids=(explorer,),
        route=(origin, destination),
    )

    result = advance_expeditions(
        (expedition,),
        civilization.population.people,
        state.world_map,
        day=4,
    )

    assert result.observations[-1].tile == destination
    assert len({item.tile for item in result.observations}) == len(result.observations)


def test_second_sighting_refreshes_instead_of_duplicating_an_observation() -> None:
    state = _state()
    civilization = state.civilizations[sorted(state.civilizations)[0]]
    explorer = civilization.population.living_ids[0]
    origin = civilization.population.people[explorer].location
    destination = _one_day_away(state.world_map, origin)
    first = advance_expeditions(
        (
            Expedition(
                expedition_id=EntityId("expedition:1"),
                explorer_ids=(explorer,),
                route=(origin, destination),
            ),
        ),
        civilization.population.people,
        state.world_map,
        day=4,
    )
    civilization.population.people[explorer].location = origin

    second = advance_expeditions(
        (
            Expedition(
                expedition_id=EntityId("expedition:2"),
                explorer_ids=(explorer,),
                route=(origin, destination),
            ),
        ),
        civilization.population.people,
        state.world_map,
        day=5,
        observations=first.observations,
    )

    assert len(second.observations) == 1
    assert second.observations[0].observed_day == 5


def test_dead_explorer_fails_without_an_observation() -> None:
    state = _state()
    civilization = state.civilizations[sorted(state.civilizations)[0]]
    explorer = civilization.population.living_ids[0]
    origin = civilization.population.people[explorer].location
    civilization.population.people[explorer].alive = False
    expedition = Expedition(
        expedition_id=EntityId("expedition:dead"),
        explorer_ids=(explorer,),
        route=(origin, state.world_map.neighbors(origin)[0]),
    )

    result = advance_expeditions(
        (expedition,),
        civilization.population.people,
        state.world_map,
        day=4,
    )

    assert result.expeditions[0].status is ExpeditionStatus.FAILED
    assert result.observations == ()


def _council_with(order_of):  # type: ignore[no-untyped-def]
    """Advance one day with the first civilization's council giving one order."""
    from sovereign_world.commands import CommandEnvelope, build_council_report
    from sovereign_world.engine import advance_day
    from sovereign_world.rng import StableRng

    state = _state()
    first = sorted(state.civilizations)[0]
    report = build_council_report(state, first)
    order = order_of(report)

    class Council:
        def decide(self, report):  # type: ignore[no-untyped-def]
            commands = (order,) if report.civilization_id == first else ()
            return CommandEnvelope(
                schema_version=1,
                civilization_id=report.civilization_id,
                council_day=report.day,
                correlation_id=report.report_id,
                commands=commands,
            )

    sovereigns = {civilization_id: Council() for civilization_id in state.civilizations}
    return first, advance_day(state, StableRng(21), sovereigns=sovereigns)


def test_explorers_named_in_any_order_set_out_in_id_order() -> None:
    """A council (a model) may name its explorers in any order; the engine used to fail the
    whole day on an expedition not given in id order (sealed trial, found by calibration)."""
    from sovereign_world.commands import DirectOrder, DirectOrderKind

    def order(report):  # type: ignore[no-untyped-def]
        explorers = tuple(sorted(report.person_ids[-2:], reverse=True))
        route = (report.start_center, _one_day_away(_state().world_map, report.start_center))
        return DirectOrder(
            command_id="explore",
            kind=DirectOrderKind.START_EXPEDITION,
            expedition_id=EntityId("expedition:any-order"),
            explorer_ids=explorers,
            route=route,
        )

    first, transition = _council_with(order)
    (expedition,) = [
        item
        for item in transition.state.civilizations[first].expeditions
        if item.expedition_id == "expedition:any-order"
    ]
    assert list(expedition.explorer_ids) == sorted(expedition.explorer_ids)


def test_an_explorer_named_twice_is_refused() -> None:
    from sovereign_world.commands import DirectOrder, DirectOrderKind

    def order(report):  # type: ignore[no-untyped-def]
        one = report.person_ids[-1]
        route = (report.start_center, _one_day_away(_state().world_map, report.start_center))
        return DirectOrder(
            command_id="explore",
            kind=DirectOrderKind.START_EXPEDITION,
            expedition_id=EntityId("expedition:twice"),
            explorer_ids=(one, one),
            route=route,
        )

    first, transition = _council_with(order)
    codes = [
        event.payload.get("code")
        for event in transition.events.events
        if event.kind == "command_rejected"
    ]
    assert codes == ["invalid_expedition"]
    assert not transition.state.civilizations[first].expeditions or all(
        item.expedition_id != "expedition:twice"
        for item in transition.state.civilizations[first].expeditions
    )
