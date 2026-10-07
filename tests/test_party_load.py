"""A war party that loses fighters to capture keeps a load its remaining carriers can bear
(found by the balance calibration: a raid that lost one of eight at home stopped the world)."""

from __future__ import annotations

from sovereign_world.calibration.histories import HistorySpec, run_history
from sovereign_world.calibration.policies import POLICIES
from sovereign_world.engine import _fitted
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.logistics import Journey, JourneyKind
from sovereign_world.resources import Resource
from sovereign_world.war import WarObjective


def _party(travellers: int, packed: int, provisions: int, **cargo: int) -> Journey:
    return Journey(
        journey_id=EntityId("journey:civilization:0000000004:raid:120"),
        kind=JourneyKind.CAMPAIGN,
        sender_civilization_id=EntityId("civilization:0000000004"),
        recipient_civilization_id=EntityId("civilization:0000000001"),
        traveller_ids=tuple(EntityId(f"person:{index:010d}") for index in range(travellers)),
        route=(HexCoord(q=0, r=0), HexCoord(q=1, r=0)),
        cargo={Resource(name): count for name, count in cargo.items()},
        provisions_packed=packed,
        provisions=provisions,
        departed_day=120,
        objective=WarObjective.RAID,
    )


def test_a_party_that_still_fits_keeps_its_load_exactly() -> None:
    party = _party(8, 300, 200)
    fitted = _fitted(party, party.traveller_ids[:7])
    assert fitted == party.model_copy(update={"traveller_ids": party.traveller_ids[:7]})


def test_the_food_packed_is_cut_to_what_the_rest_can_bear() -> None:
    party = _party(8, 352, 184)
    fitted = _fitted(party, party.traveller_ids[:7])
    assert (fitted.provisions_packed, fitted.provisions) == (350, 184)
    fitted = _fitted(party, party.traveller_ids[:3])
    assert (fitted.provisions_packed, fitted.provisions) == (150, 150)


def test_gear_the_rest_cannot_bear_is_left_behind() -> None:
    party = _party(2, 30, 20, bronze_arms=60)
    fitted = _fitted(party, party.traveller_ids[:1])
    assert fitted.carry_per_person == 50
    assert (fitted.provisions_packed, fitted.provisions) == (0, 0)
    assert fitted.cargo == {Resource.BRONZE_ARMS: 50}
    Journey.model_validate(fitted.model_dump())


def test_the_raid_that_stopped_the_calibration_now_plays_on() -> None:
    rows = run_history(
        HistorySpec(seed=10, size=32, days=150, rotation=1, assignment=POLICIES, label="mixed")
    )
    assert len(rows) == 4
