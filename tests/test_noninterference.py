"""The leak test has teeth: each hidden fact really differs in the hidden world, the report
stays the same, and a report still changes when its own civilization's facts change."""

from logistics_helpers import treaty_world
from noninterference import hide_unseen

from sovereign_world.commands import build_council_report
from sovereign_world.diplomacy import TreatyEndKind
from sovereign_world.endings import Ending, EndingKind, Ruin
from sovereign_world.engine import _end_treaty
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory, Resource
from sovereign_world.territory import Settlement
from sovereign_world.war import Occupation, Siege


def _same_report(state, civilization_id) -> None:
    assert build_council_report(hide_unseen(state, civilization_id), civilization_id) == (
        build_council_report(state, civilization_id)
    )


def _far_colony(state, civilization_id) -> Settlement:
    capital = state.civilizations[civilization_id].settlements[0]
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{civilization_id.rsplit(':', 1)[-1]}-0009"),
        civilization_id=civilization_id,
        tile=HexCoord(capital.tile.q + 6, capital.tile.r),
        founded_day=0,
    )
    state.civilizations[civilization_id].settlements = (
        *state.civilizations[civilization_id].settlements,
        colony,
    )
    return colony


def test_a_report_changes_when_its_own_civilization_changes() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    before = build_council_report(state, home)
    state.civilizations[home].inventory = state.civilizations[home].inventory.model_copy(
        update={"quantities": {Resource.FOOD: 1}}
    )
    assert build_council_report(state, home) != before
    assert build_council_report(state, rival) == build_council_report(
        hide_unseen(state, rival), rival
    )


def test_an_unlearned_capture_is_hidden() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    taken = state.civilizations[home].population.living_ids[0]
    person = state.civilizations[home].population.people[taken]
    person.captive_of = rival
    person.held_at = state.civilizations[rival].settlements[0].settlement_id
    hidden = hide_unseen(state, home)
    assert hidden.civilizations[home].population.people[taken].captive_of is None
    _same_report(state, home)


def test_an_unseen_siege_and_occupation_are_hidden() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    colony = _far_colony(state, rival)
    state.sieges = (
        Siege(
            siege_id=EntityId("siege:far"),
            journey_id=EntityId("journey:far"),
            besieger_id=home,
            defender_id=rival,
            settlement_id=colony.settlement_id,
            settlement_tile=colony.tile,
            camp=HexCoord(colony.tile.q + 1, colony.tile.r),
            started_day=0,
        ),
    )
    state.occupations = (
        Occupation(
            occupation_id=EntityId("occupation:far"),
            journey_id=EntityId("journey:held"),
            occupier_id=home,
            owner_id=rival,
            settlement_id=colony.settlement_id,
            tile=colony.tile,
            started_day=0,
        ),
    )
    hidden = hide_unseen(state, rival)
    assert hidden.sieges == () and hidden.occupations == ()
    _same_report(state, rival)
    _same_report(state, home)


def test_an_unheard_treaty_ending_is_hidden() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    [treaty] = state.active_treaties
    _end_treaty(state, treaty.treaty_id, TreatyEndKind.BREACHED, home)
    [hidden] = hide_unseen(state, rival).active_treaties
    assert hidden.end_kind is TreatyEndKind.CANCELLED
    _same_report(state, rival)


def test_unseen_ruins_and_the_worlds_ending_are_hidden() -> None:
    state, home, rival, _ = treaty_world(distance=4)
    colony = _far_colony(state, rival)
    state.ruins = (
        Ruin(
            ruin_id=EntityId("ruin:far"),
            tile=colony.tile,
            former_settlement_id=colony.settlement_id,
            former_civilization_id=rival,
            since_day=0,
            store=Inventory(capacity=2_000),
        ),
    )
    state.endings = (
        Ending(kind=EndingKind.LAST_CIVILIZATION, day=0, survivor_id=home, population=1),
    )
    hidden = hide_unseen(state, home)
    assert hidden.ruins == () and hidden.endings == ()
    _same_report(state, home)
