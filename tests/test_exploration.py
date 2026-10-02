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
