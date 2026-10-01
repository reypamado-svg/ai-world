"""Phase 2 exit: scripted sovereigns play out each scenario the Civilization Layer promises.

Every scenario replays exactly, and every council report it produced is checked for
hidden knowledge: it must not change when anything its civilization has not seen or been
told changes.
"""

from pathlib import Path

import pytest
from logistics_helpers import (
    ScheduledSovereign,
    clear_journey_id,
    clear_message_id,
    linked_world,
    move_home,
    treaty_world,
)
from noninterference import assert_no_hidden_knowledge
from scenario_helpers import (
    Vanquished,
    Victor,
    Welcoming,
    assert_replays,
    envelope_of,
    run_scenario,
)

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder, DirectOrderKind
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import TreatyEndKind, TreatyKind
from sovereign_world.endings import EndingKind
from sovereign_world.engine import _change_allegiance
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.logistics import NoticeKind
from sovereign_world.resources import Inventory, Resource
from sovereign_world.scripted import BaselineSovereign
from sovereign_world.state import build_initial_state
from sovereign_world.territory import Settlement
from sovereign_world.war import WarObjective


def test_isolated_development(tmp_path: Path) -> None:
    initial = build_initial_state(
        RunManifest.new(config=WorldConfig(seed=21, width=48, height=48), engine_version="0.1.0")
    )
    days = 91

    def sovereigns():
        return {civilization_id: BaselineSovereign() for civilization_id in initial.civilizations}

    run = run_scenario(initial, sovereigns, days, tmp_path)

    final = run.final
    assert not final.active_treaties and not final.wars and not final.diplomatic_missions
    assert all(
        journey.sender_civilization_id == journey.recipient_civilization_id
        for journey in final.journeys
    ), "no one crosses to another civilization"
    builders = {event.actor_id for event in run.of("project_started")}
    for civilization_id, civilization in final.civilizations.items():
        assert civilization.population.living_ids
        assert str(civilization_id) in builders, "each builds on its own"
        assert all(not report.contacts for report in run.reports(civilization_id))
    assert assert_no_hidden_knowledge(run.councils) == 4 * 4
    assert_replays(run, initial, sovereigns, days)


def _first_contact_world():
    _, state, home, rival, route = linked_world(distance=5)
    state.civilizations[home].contacts = ()
    state.civilizations[rival].contacts = ()
    return state, home, rival, route


def test_first_contact(tmp_path: Path) -> None:
    initial, home, rival, route = _first_contact_world()
    explorer = initial.civilizations[home].population.living_ids[0]
    explore = DirectOrder(
        command_id="explore",
        kind=DirectOrderKind.START_EXPEDITION,
        expedition_id=EntityId("expedition:west"),
        explorer_ids=(explorer,),
        route=route,
    )

    def sovereigns():
        return {home: ScheduledSovereign({0: (explore,)})}

    run = run_scenario(initial, sovereigns, 31, tmp_path)

    [sighted] = [event for event in run.of("foreign_settlement_sighted") if event.actor_id == home]
    assert sighted.subject_id == str(rival)
    first, later = run.reports(home)
    assert first.contacts == () and [item.civilization_id for item in later.contacts] == [rival]
    assert all(
        home not in {item.civilization_id for item in report.contacts}
        for report in run.reports(rival)
    ), "the rival saw no one"
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, 31)


class AcceptAll:
    """Accept every treaty offer that has reached it and is not yet in force."""

    def __init__(self, route_home: tuple[HexCoord, ...]) -> None:
        self.route_home = route_home

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        standing = {treaty.treaty_id for treaty in report.treaties}
        orders = [
            DirectOrder(
                command_id=f"accept:{message.treaty_offer.offer_id}",
                kind=DirectOrderKind.ACCEPT_TREATY,
                treaty_id=message.treaty_offer.offer_id,
                message_id=EntityId(clear_message_id("accept", start_day=report.day, days=12)),
                ambassador_id=report.person_ids[-1],
                recipient_civilization_id=message.sender_civilization_id,
                message_text="We accept.",
                route=self.route_home,
            )
            for message in report.received_messages
            if message.treaty_offer is not None and message.treaty_offer.offer_id not in standing
        ]
        return envelope_of(report, orders)


class Faithless(AcceptAll):
    """Accept, then at the second council break the treaty and say so."""

    def __init__(self, route_home: tuple[HexCoord, ...], partner: EntityId) -> None:
        super().__init__(route_home)
        self.partner = partner

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        if report.day != 60:
            return super().decide(report)
        [treaty] = [item for item in report.treaties if item.in_force]
        return envelope_of(
            report,
            [
                DirectOrder(
                    command_id="repudiate",
                    kind=DirectOrderKind.REPUDIATE_TREATY,
                    treaty_id=treaty.treaty_id,
                ),
                DirectOrder(
                    command_id="farewell",
                    kind=DirectOrderKind.SEND_MESSAGE,
                    message_id=EntityId(clear_message_id("farewell", start_day=60, days=12)),
                    ambassador_id=report.person_ids[-2],
                    recipient_civilization_id=self.partner,
                    message_text="We will trade with you no longer.",
                    route=self.route_home,
                ),
            ],
        )


def test_treaty_made_and_broken(tmp_path: Path) -> None:
    _, initial, home, rival, route = linked_world(distance=3)
    offer = DirectOrder(
        command_id="offer",
        kind=DirectOrderKind.OFFER_TREATY,
        treaty_id=EntityId("treaty:trade"),
        treaty_kind=TreatyKind.TRADE,
        message_id=EntityId(clear_message_id("offer", days=12)),
        ambassador_id=initial.civilizations[home].population.living_ids[-1],
        recipient_civilization_id=rival,
        message_text="Let us trade.",
        route=route,
    )

    def sovereigns():
        return {
            home: ScheduledSovereign({0: (offer,)}),
            rival: Faithless(tuple(reversed(route)), home),
        }

    run = run_scenario(initial, sovereigns, 91, tmp_path)

    assert run.of("treaty_activated") and run.of("treaty_breached")
    reports = {report.day: report for report in run.reports(home)}
    [standing] = reports[60].treaties
    assert standing.in_force, "the breach is not yet known at home"
    [broken] = reports[90].treaties
    assert broken.end_kind is TreatyEndKind.BREACHED and broken.notice_day is not None
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, 91)


def test_trade(tmp_path: Path) -> None:
    initial, home, rival, route = treaty_world(distance=4)
    ship = DirectOrder(
        command_id="caravan",
        kind=DirectOrderKind.DISPATCH_SHIPMENT,
        journey_id=EntityId(clear_journey_id("caravan", days=12)),
        treaty_id=EntityId("treaty:trade"),
        recipient_civilization_id=rival,
        traveller_ids=initial.civilizations[home].population.living_ids[:3],
        route=route,
        cargo={Resource.STONE: 30},
    )

    def sovereigns():
        return {home: ScheduledSovereign({0: (ship,)})}

    run = run_scenario(initial, sovereigns, 31, tmp_path)

    [received] = run.of("shipment_received")
    assert received.payload["units"] == 30
    _, rival_later = run.reports(rival)
    assert any(item.kind is NoticeKind.SHIPMENT_RECEIVED for item in rival_later.logistics_notices)
    assert not any(
        item.kind is NoticeKind.SHIPMENT_RECEIVED
        for report in run.reports(home)
        for item in report.logistics_notices
    )
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, 31)


def test_war(tmp_path: Path) -> None:
    initial, home, rival, route = treaty_world(distance=4)
    fields = HexCoord(route[-1].q, route[-1].r - 3)
    rival_people = initial.civilizations[rival].population.people
    for person_id in sorted(rival_people)[10:]:
        rival_people[person_id].location = fields
    living = initial.civilizations[home].population.living_ids

    def raid(day: int, fighters: tuple[EntityId, ...], axes: int) -> DirectOrder:
        return DirectOrder(
            command_id=f"raid:{day}",
            kind=DirectOrderKind.SEND_WAR_PARTY,
            journey_id=EntityId(f"journey:raid:{day}"),
            recipient_civilization_id=rival,
            traveller_ids=fighters,
            route=route,
            cargo={Resource.AXE: axes} if axes else {},
            war_objective=WarObjective.RAID,
        )

    declare = DirectOrder(
        command_id="declare",
        kind=DirectOrderKind.DECLARE_WAR,
        message_id=EntityId("message:war"),
        ambassador_id=living[-1],
        recipient_civilization_id=rival,
        message_text="Your granaries will be ours.",
        route=route,
    )

    def sovereigns():
        return {
            home: ScheduledSovereign(
                {0: (declare, raid(0, living[:4], 0)), 30: (raid(30, living[4:24], 8),)}
            )
        }

    run = run_scenario(initial, sovereigns, 61, tmp_path)

    [war] = run.final.wars
    assert war.defender_learned_day is not None and len(run.final.battles) == 2
    rival_first = run.reports(rival)[0]
    assert rival_first.wars == (), "the rival has not yet heard"
    assert [report.won for report in run.reports(home)[-1].war_reports] == [False, True]
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, 61)


def _surrender_world():
    initial, first, second, route = treaty_world(distance=4)
    loser = initial.civilizations[second]
    tile = next(
        coord
        for coord in sorted(
            HexCoord(route[-1].q + dq, route[-1].r + dr)
            for dq in range(-4, 5)
            for dr in range(-4, 5)
        )
        if coord.distance(route[-1]) == 3
        and coord not in route
        and initial.world_map.contains(coord)
        and initial.world_map.tile(coord).terrain is not Terrain.WATER
    )
    colony = Settlement(
        settlement_id=EntityId(f"settlement:{second.rsplit(':', 1)[-1]}-0002"),
        civilization_id=second,
        tile=tile,
        founded_day=0,
    )
    loser.settlements = (*loser.settlements, colony)
    for person_id in loser.population.living_ids[-4:]:
        loser.population.people[person_id].location = tile
    loser.stores = {
        colony.settlement_id: Inventory(capacity=2_000, quantities={Resource.FOOD: 400})
    }
    return initial, first, second, route, colony.settlement_id


@pytest.mark.soak
def test_surrender(tmp_path: Path) -> None:
    initial, first, second, route, colony = _surrender_world()

    def sovereigns():
        return {
            first: Victor(second, route),
            second: Vanquished(first, tuple(reversed(route)), colony),
        }

    run = run_scenario(initial, sovereigns, 150, tmp_path)

    assert run.of("peace_made") and run.of("settlement_ceded")
    assert any(item.settlement_id == colony for item in run.final.civilizations[first].settlements)
    peace = next(item for item in run.final.active_treaties if item.kind is TreatyKind.PEACE)
    assert peace.terms is not None and peace.terms.ceded_settlement == colony
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, 150)


def test_assimilation(tmp_path: Path) -> None:
    initial, home, rival, _ = treaty_world(distance=4)
    people = initial.civilizations[rival].population
    moving = tuple(
        person_id
        for person_id in people.living_ids
        if not any(birth.parent_ids[0] == person_id for birth in people.scheduled_births)
    )[:4]
    _change_allegiance(initial, moving, rival, home, "release")
    for person_id in moving:
        newcomer = initial.civilizations[home].population.people[person_id]
        newcomer.assimilation = 94
        newcomer.languages = {home: 60}

    def sovereigns():
        return {}

    run = run_scenario(initial, sovereigns, 61, tmp_path)

    assimilated = {event.subject_id for event in run.of("person_assimilated")}
    assert assimilated >= {
        str(person_id)
        for person_id in moving
        if run.final.civilizations[home].population.people[person_id].alive
    }
    first, *_, last = run.reports(home)
    assert first.cultures.get(rival, 0) == len(moving) and first.assimilating == len(moving)
    assert last.cultures.get(rival, 0) == 0 and last.ancestries[rival] >= 1
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, 61)


def _last_world():
    initial, home, rival, route = treaty_world(seed=0, distance=3)
    capital = initial.civilizations[home].settlements[0].tile
    nearby = [
        tile
        for tile in initial.world_map.neighbors(capital)
        if initial.world_map.tile(tile).terrain is not Terrain.WATER and tile not in route
    ]
    for index, civilization_id in enumerate(
        item for item in sorted(initial.civilizations) if item not in {home, rival}
    ):
        # Every other civilization has already died out, within sight of home.
        civilization = initial.civilizations[civilization_id]
        move_home(civilization, nearby[index])
        for person in civilization.population.people.values():
            person.alive, person.death_day, person.health_bp = False, 0, 0
        civilization.population = civilization.population.model_copy(
            update={"scheduled_births": ()}
        )
    # The rival is down to a few people, out in the fields and away from home.
    rival_people = initial.civilizations[rival].population.people
    for person_id, person in rival_people.items():
        if person_id in sorted(rival_people)[:4]:
            person.location = route[-2]
        else:
            person.alive, person.death_day, person.health_bp = False, 0, 0
    initial.civilizations[rival].population = initial.civilizations[rival].population.model_copy(
        update={"scheduled_births": ()}
    )
    return initial, home, rival


@pytest.mark.soak
def test_extinction_and_final_survivor(tmp_path: Path) -> None:
    initial, home, rival = _last_world()
    days = 330

    def sovereigns():
        return {home: Welcoming()}

    run = run_scenario(initial, sovereigns, days, tmp_path)

    assert run.final.civilizations[rival].eliminated_day is not None
    assert any(ruin.former_civilization_id == rival for ruin in run.final.ruins)
    known = run.final.civilizations[home].fallen
    assert set(known) == set(run.final.civilizations) - {home}
    [ending] = [item for item in run.final.endings if item.kind is EndingKind.LAST_CIVILIZATION]
    assert ending.survivor_id == home
    shown = [report for report in run.reports(home) if report.endings]
    assert shown, "home comes to know it is the last"
    assert all(report.day > max(known.values()) for report in shown)
    assert not any(
        report.endings for report in run.reports(home) if report.day <= max(known.values())
    )
    assert_no_hidden_knowledge(run.councils)
    assert_replays(run, initial, sovereigns, days)
