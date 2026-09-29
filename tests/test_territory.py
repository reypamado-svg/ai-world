from logistics_helpers import OneShotSovereign, treaty_world

from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.engine import advance_day
from sovereign_world.exploration import Expedition
from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.territory import (
    CHALLENGE_DAYS,
    Settlement,
    Territory,
    TileOwner,
    advance_territory,
    influence_field,
    settlement_strength,
    supply_connected,
)

RED = EntityId("civilization:0000000001")
BLUE = EntityId("civilization:0000000002")


def _plain(width: int = 13, height: int = 5, **terrain: Terrain) -> WorldMap:
    tiles = []
    for r in range(height):
        for q in range(width):
            kind = terrain.get(f"{q},{r}", Terrain.GRASSLAND)
            tiles.append(Tile(HexCoord(q, r), kind, 500, 500, 500, 500, 0, 0, 0))
    return WorldMap(width=width, height=height, tiles=tuple(tiles))


def _town(civilization: EntityId, q: int, r: int = 2, *, capital: bool = True) -> Settlement:
    return Settlement(
        settlement_id=EntityId(f"settlement:{civilization[-1]}-{q}"),
        civilization_id=civilization,
        tile=HexCoord(q, r),
        founded_day=0,
        capital=capital,
    )


def _days(world, settlements, residents, days, territory=None):
    territory = territory or Territory()
    changes = []
    for day in range(days):
        result = advance_territory(territory, world, settlements, residents, day)
        territory = result.territory
        changes.extend((day, change) for change in result.changes)
    return territory, changes


def test_a_settlement_projects_more_with_more_people() -> None:
    assert settlement_strength(0) == 0
    assert settlement_strength(1) == 48
    assert settlement_strength(32) == 80
    assert settlement_strength(100) == 120


def test_influence_fades_with_terrain_and_stops_at_water() -> None:
    world = _plain(**{"3,2": Terrain.MOUNTAIN, "7,2": Terrain.WATER})
    field = influence_field(world, [(HexCoord(5, 2), 80)])

    assert field[HexCoord(5, 2)] == 80
    assert field[HexCoord(4, 2)] == 70, "one grassland day"
    assert field[HexCoord(3, 2)] == 40, "a mountain costs three days"
    assert HexCoord(7, 2) not in field, "water carries no influence"
    assert max(field.values()) == 80
    assert all(value > 0 for value in field.values())


def test_the_strongest_source_counts_not_the_sum() -> None:
    world = _plain()
    one = influence_field(world, [(HexCoord(4, 2), 80)])
    two = influence_field(world, [(HexCoord(4, 2), 80), (HexCoord(6, 2), 80)])

    assert two[HexCoord(5, 2)] == one[HexCoord(5, 2)] == 70


def test_control_forms_gradually_and_leaves_a_frontier() -> None:
    world = _plain()
    town = _town(RED, 6)
    residents = {town.settlement_id: 32}

    territory, _ = _days(world, [town], residents, 13)
    assert territory.owner_of() == {town.tile: RED}, "only the anchored settlement tile yet"

    territory, _ = _days(world, [town], residents, 1, territory)
    owners = territory.owner_of()
    assert owners[HexCoord(7, 2)] == RED, "held control reached 40 after 14 days"
    assert owners[HexCoord(10, 2)] == RED, "four grassland days out: influence exactly 40"
    assert HexCoord(11, 2) not in owners, "five days out: influence 30 is never enough"
    assert HexCoord(12, 2) not in owners, "frontier beyond reach"


def test_an_abandoned_settlement_loses_its_land_slowly() -> None:
    world = _plain()
    town = _town(RED, 6)
    territory, _ = _days(world, [town], {town.settlement_id: 32}, 40)
    assert territory.owner_of()[HexCoord(7, 2)] == RED

    territory, changes = _days(world, [town], {town.settlement_id: 0}, 30, territory)
    lost = {change.tile: day for day, change in changes if not change.gained}
    assert HexCoord(6, 2) in lost, "an empty settlement is no longer anchored"
    assert lost[HexCoord(7, 2)] > 0, "loss is gradual, not immediate"
    assert territory.owners == ()


def test_a_rival_must_out_hold_the_owner_for_a_week() -> None:
    world = _plain(width=15)
    red = _town(RED, 4)
    blue = _town(BLUE, 10)
    residents = {red.settlement_id: 32, blue.settlement_id: 32}
    territory, _ = _days(world, [red, blue], residents, 60)
    middle = HexCoord(7, 2)
    assert middle not in territory.owner_of(), "an equal tie stays unowned"

    # Red grows first and takes the middle; then Blue grows much stronger.
    grown = {red.settlement_id: 100, blue.settlement_id: 32}
    territory, _ = _days(world, [red, blue], grown, 40, territory)
    assert territory.owner_of()[middle] == RED

    stronger = {red.settlement_id: 32, blue.settlement_id: 400}
    first_challenge = transfer = None
    for day in range(60):
        result = advance_territory(territory, world, [red, blue], stronger, day)
        territory = result.territory
        if first_challenge is None and any(
            item.tile == middle and item.civilization_id == BLUE for item in territory.challenges
        ):
            first_challenge = day
        if any(change.tile == middle and change.gained for change in result.changes):
            [gain] = [
                change for change in result.changes if change.tile == middle and change.gained
            ]
            assert (gain.civilization_id, gain.previous_owner) == (BLUE, RED)
            transfer = day
            break
    assert first_challenge is not None and transfer is not None
    assert transfer - first_challenge == CHALLENGE_DAYS - 1, "seven consecutive days"


def test_contested_tiles_are_where_two_civilizations_both_hold_on() -> None:
    world = _plain(width=15)
    red = _town(RED, 4)
    blue = _town(BLUE, 10)
    territory, _ = _days(world, [red, blue], {red.settlement_id: 32, blue.settlement_id: 32}, 60)

    assert HexCoord(7, 2) in territory.contested()
    assert HexCoord(4, 2) not in territory.contested()


def test_a_settlement_cut_off_from_its_capital_projects_half() -> None:
    world = _plain(width=15, height=1)
    capital = _town(RED, 1, r=0)
    colony = _town(RED, 13, r=0, capital=False)
    owners = {HexCoord(7, 0): BLUE}

    assert not supply_connected(world, colony.tile, capital.tile, RED, owners)
    assert supply_connected(world, colony.tile, capital.tile, RED, {})

    blocked = Territory(owners=(TileOwner(tile=HexCoord(7, 0), civilization_id=BLUE, since_day=0),))
    residents = {capital.settlement_id: 32, colony.settlement_id: 32}
    result = advance_territory(blocked, world, [capital, colony], residents, 0)
    assert result.cut_off == (colony.settlement_id,)


def test_claims_never_change_control() -> None:
    base, sender, _, route = treaty_world(None)
    far = tuple(
        tile.coord for tile in base.world_map.tiles if tile.coord.distance(route[0]) in (8, 9)
    )[:40]
    claim = DirectOrder(command_id="claim", kind=DirectOrderKind.CLAIM_BORDER, claimed_tiles=far)

    def run(sovereigns):
        state = base.model_copy(deep=True)
        rng = StableRng(state.config.seed)
        kinds: list[str] = []
        for _ in range(20):
            result = advance_day(state, rng, sovereigns=sovereigns)
            state = result.state
            kinds.extend(event.kind for event in result.events.events)
        return state, kinds

    claimed, kinds = run({sender: OneShotSovereign(claim)})
    unclaimed, _ = run({sender: OneShotSovereign()})

    assert "claim_recorded" in kinds
    assert claimed.civilizations[sender].claims[0].tiles == tuple(sorted(far))
    assert claimed.territory == unclaimed.territory


def test_reports_show_own_land_and_only_observed_foreign_control() -> None:
    state, sender, rival, _ = treaty_world(None)
    rng = StableRng(state.config.seed)
    for _ in range(20):
        state = advance_day(state, rng).state
    report = build_council_report(state, sender)
    owners = state.territory.owner_of()

    assert set(report.controlled_tiles) == {
        tile for tile, owner in owners.items() if owner == sender
    }
    viewed = {view.tile: view for view in report.observed_control}
    assert set(viewed) <= set(state.civilizations[sender].known_tiles) | {
        tile for tile in state.world_map.neighbors(state.civilizations[sender].start_center)
    } | {state.civilizations[sender].start_center}
    rival_land = {tile for tile, owner in owners.items() if owner == rival}
    unseen_rival_land = rival_land - set(viewed)
    assert unseen_rival_land, "some rival land is out of sight"
    assert all(
        view.owner != rival or view.as_of_day <= state.day for view in report.observed_control
    )
    assert "influence" not in report.model_dump_json()


def test_explorers_record_who_owned_the_land_they_saw() -> None:
    state, sender, rival, route = treaty_world(None)
    rng = StableRng(state.config.seed)
    for _ in range(20):
        state = advance_day(state, rng).state
    rival_home = state.civilizations[rival].start_center
    explorer = state.civilizations[sender].population.living_ids[3]
    state.civilizations[sender].population.people[explorer].location = route[-2]
    state.civilizations[sender].expeditions = (
        Expedition(
            expedition_id=EntityId("expedition:look"),
            explorer_ids=(explorer,),
            route=(route[-2], rival_home),
        ),
    )

    state = advance_day(state, rng).state

    view = next(
        item
        for item in build_council_report(state, sender).observed_control
        if item.tile == rival_home
    )
    assert view.owner == rival
    assert view.as_of_day == state.day - 1
