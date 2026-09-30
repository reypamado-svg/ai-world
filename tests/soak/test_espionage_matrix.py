"""Seeded spying: parties watch, send couriers, come home or are caught, and replay exactly."""

import pytest
from logistics_helpers import treaty_world

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.engine import advance_day
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, state_hash

DAYS = 150


class Spymaster:
    """Send spies at the first council, and a courier home at each later one."""

    def __init__(self, enemy: EntityId, route: tuple[HexCoord, ...], spies: int) -> None:
        self.enemy = enemy
        self.route = route
        self.spies = spies

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders: list[DirectOrder] = []
        if report.day == 0:
            orders.append(
                DirectOrder(
                    command_id="spy",
                    kind=DirectOrderKind.SEND_SPY,
                    journey_id=EntityId(f"journey:{report.civilization_id}:spy"),
                    recipient_civilization_id=self.enemy,
                    traveller_ids=report.person_ids[-self.spies :],
                    route=self.route,
                    watch_days=80,
                )
            )
        for index, party in enumerate(report.spy_missions):
            if party.watching and party.findings is not None and len(party.traveller_ids) > 1:
                orders.append(
                    DirectOrder(
                        command_id=f"courier:{index}",
                        kind=DirectOrderKind.SEND_COURIER,
                        journey_id=party.journey_id,
                        traveller_ids=(party.traveller_ids[0],),
                        route=tuple(reversed(self.route)),
                    )
                )
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=tuple(orders),
        )


def _simulate(initial: WorldState, home: EntityId, spymaster: Spymaster):
    state = initial.model_copy(deep=True)
    rng = StableRng(state.config.seed)
    kinds: list[str] = []
    for _ in range(DAYS):
        transition = advance_day(state, rng, sovereigns={home: spymaster})
        state = transition.state
        kinds.extend(event.kind for event in transition.events.events)
    return state, kinds


@pytest.mark.soak
@pytest.mark.parametrize("seed", range(8))
def test_seeded_spying_ends_in_reports_or_prisoners_and_replays_exactly(seed: int) -> None:
    initial, home, rival, route = treaty_world(seed=seed, distance=3 + seed % 4)
    if seed % 2:
        # Half the parties speak the language they will listen to.
        for person in initial.civilizations[home].population.people.values():
            person.languages = {rival: 80}
    spymaster = Spymaster(rival, route, spies=2 + seed % 3)

    final, kinds = _simulate(initial, home, spymaster)
    rerun, rerun_kinds = _simulate(initial, home, spymaster)

    assert "spies_dispatched" in kinds
    reports = final.civilizations[home].spy_reports
    caught = final.civilizations[rival].caught_spies
    assert reports or caught, "every watch ends in findings at home or spies in chains"
    for report in reports:
        assert report.estimate.civilization_id == rival
        assert report.estimate.day <= report.delivered_day
    assert all(item.sender_civilization_id == home for item in caught)
    assert all(
        final.civilizations[home].population.people[item.person_id].captive_of == rival
        or not final.civilizations[home].population.people[item.person_id].alive
        or "captive_freed" in kinds
        for item in caught
    )
    assert state_hash(rerun) == state_hash(final)
    assert rerun_kinds == kinds
