"""Scripted policies for balance calibration (the sealed trial, roadmap phase 5).

Each policy is the baseline's steady economy plus one trait, decided from the council report
alone, so thousands of histories can ask how a start fares under different play:

- ``builder``: the baseline, nothing more;
- ``expander``: sends settlers to the nearest open site with water, every 90 days from day 60;
- ``trader``: explores until it meets a neighbour it can walk to, offers it trade, accepts trade
  offered, and sends a small caravan to each partner at every council;
- ``raider``: explores until it meets a neighbour it can walk to, declares war on it, then sends
  a band of eight to raid it at every council.

The trait's orders come first and the baseline's fill the rest of the council's eight; people
for the trait are taken from the idle grown-ups the report names, from the far end of that list,
so they are not the baseline's builders. Every policy is deterministic: the same report gives the
same orders.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sovereign_world.commands import (
    Command,
    CommandEnvelope,
    CouncilReport,
    DirectOrder,
    DirectOrderKind,
)
from sovereign_world.diplomacy import ActiveTreaty, TreatyKind
from sovereign_world.hexmap import HexCoord, Terrain
from sovereign_world.ids import EntityId
from sovereign_world.resources import Resource
from sovereign_world.scripted import COUNCIL_ORDERS, RESERVED_HANDS, plan_baseline_commands
from sovereign_world.war import WarObjective

POLICIES = ("builder", "expander", "trader", "raider")
"""The calibration policies, in a fixed order."""
TRAIT_ORDERS = 2
"""At most this many of a council's orders go to the policy's trait."""
GROWN_YEARS = 16
EXPLORE_STEPS = 24
"""How far explorers walk, out and back: a neighbour's capital is at least 12 tiles away, and an
explorer sees only a tile or so either side of the path."""
RAID_BAND = 8
CARAVAN = {Resource.STONE: 20, Resource.TIMBER: 20}


def _idle(report: CouncilReport) -> list[EntityId]:
    """Idle grown-ups at the capital that the report names, outside the baseline's reserved
    hands, last first (every trait's journey starts from the capital)."""
    reserved = set(report.person_ids[:RESERVED_HANDS])
    capital = next(
        (settlement.settlement_id for settlement in report.settlements if settlement.capital), None
    )
    idle = [
        person.person_id
        for person in report.notable_people
        if person.duty is None
        and person.age_years >= GROWN_YEARS
        and person.settlement_id == capital
        and person.person_id not in reserved
    ]
    return sorted(idle, reverse=True)


def _land_routes(report: CouncilReport) -> dict[HexCoord, tuple[HexCoord, ...]]:
    """Shortest routes over known land from the start, in a fixed order."""
    land = {tile for tile, terrain in report.known_terrain if terrain is not Terrain.WATER}
    deep = {
        frozenset((view.tile, view.across)) for view in report.known_rivers if view.depth == "deep"
    }
    home = report.start_center
    routes: dict[HexCoord, tuple[HexCoord, ...]] = {home: (home,)}
    frontier = [home]
    while frontier:
        tile = frontier.pop(0)
        for neighbor in sorted(tile.neighbors()):
            if (
                neighbor in land
                and neighbor not in routes
                and frozenset((tile, neighbor)) not in deep
            ):
                routes[neighbor] = (*routes[tile], neighbor)
                frontier.append(neighbor)
    return routes


def _reachable_contacts(report: CouncilReport) -> list[tuple[EntityId, tuple[HexCoord, ...]]]:
    """Neighbours met whose settlement can be walked to over known land, nearest first."""
    routes = _land_routes(report)
    found = [
        (len(routes[contact.settlement]), contact.civilization_id, routes[contact.settlement])
        for contact in report.contacts
        if contact.settlement in routes
    ]
    best: dict[EntityId, tuple[int, tuple[HexCoord, ...]]] = {}
    for length, civilization_id, route in sorted(found):
        best.setdefault(civilization_id, (length, route))
    return [(civ, route) for civ, (_, route) in sorted(best.items(), key=lambda item: item[1][0])]


def _explore(report: CouncilReport, people: list[EntityId]) -> list[DirectOrder]:
    """Two explorers, a straight line in one of the six directions, turning each council."""
    if len(people) < 2:
        return []
    terrain = dict(report.known_terrain)
    deep = {
        frozenset((view.tile, view.across)) for view in report.known_rivers if view.depth == "deep"
    }
    council = report.day // 30
    start = report.start_center
    for turn in range(6):
        direction = sorted(start.neighbors())[(council + turn) % 6]
        dq, dr = direction.q - start.q, direction.r - start.r
        route = [start]
        for _ in range(EXPLORE_STEPS):
            here = route[-1]
            ahead = HexCoord(here.q + dq, here.r + dr)
            off_map = ahead.q < 0 or ahead.r < 0
            if off_map or terrain.get(ahead) is Terrain.WATER or frozenset((here, ahead)) in deep:
                break
            route.append(ahead)
        if len(route) > 2:
            return [
                DirectOrder(
                    command_id=f"explore:{report.day}",
                    kind=DirectOrderKind.START_EXPEDITION,
                    expedition_id=EntityId(
                        f"expedition:{report.civilization_id}:explore:{report.day}"
                    ),
                    explorer_ids=tuple(sorted(people[:2])),
                    route=tuple(route),
                    priority=70,
                )
            ]
    return []


def _expander(report: CouncilReport) -> list[DirectOrder]:
    if report.day < 60 or (report.day - 60) % 90:
        return []
    people = _idle(report)
    if len(people) < 4:
        return []
    taken = {settlement.tile for settlement in report.settlements} | {
        contact.settlement for contact in report.contacts
    }
    mine = {None, report.civilization_id}
    foreign = {view.tile for view in report.observed_control if view.owner not in mine}
    water = {tile for tile, terrain in report.known_terrain if terrain is Terrain.WATER}
    rivers = {end for river in report.known_rivers for end in (river.tile, river.across)}
    routes = _land_routes(report)
    sites = sorted(
        (len(route), tile)
        for tile, route in routes.items()
        if tile not in foreign
        and all(tile.distance(other) >= 3 for other in taken)
        and (tile in rivers or any(neighbor in water for neighbor in tile.neighbors()))
    )
    if not sites:
        return []
    return [
        DirectOrder(
            command_id=f"found:{report.day}",
            kind=DirectOrderKind.FOUND_SETTLEMENT,
            journey_id=EntityId(f"journey:{report.civilization_id}:found:{report.day}"),
            traveller_ids=tuple(sorted(people[:4])),
            route=routes[sites[0][1]],
        )
    ]


def _other(treaty: ActiveTreaty, civilization_id: EntityId) -> EntityId:
    """The other side of a treaty."""
    if treaty.proposer_civilization_id == civilization_id:
        return treaty.recipient_civilization_id
    return treaty.proposer_civilization_id


def _trader(report: CouncilReport) -> list[DirectOrder]:
    people = _idle(report)
    contacts = _reachable_contacts(report)
    if not contacts:
        return _explore(report, people)
    orders: list[DirectOrder] = []
    routes = dict(contacts)
    trades = [
        treaty
        for treaty in sorted(report.treaties, key=lambda item: item.treaty_id)
        if treaty.kind is TreatyKind.TRADE and treaty.ended_day is None
    ]
    partners = {_other(treaty, report.civilization_id) for treaty in trades}
    offered = {
        message.sender_civilization_id: message.treaty_offer.offer_id
        for message in report.received_messages
        if message.treaty_offer is not None and message.treaty_offer.kind is TreatyKind.TRADE
    }
    # Accept trade offered by a neighbour it can reach.
    for civilization_id, offer_id in sorted(offered.items()):
        if civilization_id in partners or civilization_id not in routes or not people:
            continue
        orders.append(
            DirectOrder(
                command_id=f"accept:{report.day}:{civilization_id}",
                kind=DirectOrderKind.ACCEPT_TREATY,
                treaty_id=offer_id,
                message_id=EntityId(f"message:{report.civilization_id}:accept:{report.day}"),
                ambassador_id=people.pop(0),
                recipient_civilization_id=civilization_id,
                message_text="We accept your trade.",
                route=routes[civilization_id],
            )
        )
        partners.add(civilization_id)
        break
    # Offer trade to the nearest neighbour without a treaty, once a year.
    for civilization_id, route in contacts:
        if civilization_id in partners or civilization_id in offered or not people:
            continue
        if report.day % 360 < 30 or len(orders) == 0:
            orders.append(
                DirectOrder(
                    command_id=f"offer:{report.day}:{civilization_id}",
                    kind=DirectOrderKind.OFFER_TREATY,
                    treaty_id=EntityId(f"treaty:trade:{report.civilization_id}:{civilization_id}"),
                    treaty_kind=TreatyKind.TRADE,
                    message_id=EntityId(f"message:{report.civilization_id}:offer:{report.day}"),
                    ambassador_id=people.pop(0),
                    recipient_civilization_id=civilization_id,
                    message_text="Let us trade.",
                    route=route,
                )
            )
        break
    # A small caravan to each partner.
    for treaty in trades:
        partner = _other(treaty, report.civilization_id)
        if len(people) < 2 or partner not in routes:
            continue
        orders.append(
            DirectOrder(
                command_id=f"ship:{report.day}:{partner}",
                kind=DirectOrderKind.DISPATCH_SHIPMENT,
                journey_id=EntityId(f"journey:{report.civilization_id}:ship:{report.day}"),
                treaty_id=treaty.treaty_id,
                recipient_civilization_id=partner,
                traveller_ids=tuple(sorted((people.pop(0), people.pop(0)))),
                route=routes[partner],
                cargo=dict(CARAVAN),
            )
        )
        break
    return orders


def _raider(report: CouncilReport) -> list[DirectOrder]:
    people = _idle(report)
    contacts = _reachable_contacts(report)
    if not contacts:
        return _explore(report, people)
    enemy, route = contacts[0]
    sides = {report.civilization_id, enemy}
    at_war = any(
        war.ended_day is None and {war.aggressor_id, war.defender_id} == sides
        for war in report.wars
    )
    orders: list[DirectOrder] = []
    if not at_war and people:
        orders.append(
            DirectOrder(
                command_id=f"declare:{report.day}",
                kind=DirectOrderKind.DECLARE_WAR,
                message_id=EntityId(f"message:{report.civilization_id}:war:{report.day}"),
                ambassador_id=people.pop(0),
                recipient_civilization_id=enemy,
                message_text="War.",
                route=route,
            )
        )
    elif at_war and len(people) >= RAID_BAND:
        orders.append(
            DirectOrder(
                command_id=f"raid:{report.day}",
                kind=DirectOrderKind.SEND_WAR_PARTY,
                journey_id=EntityId(f"journey:{report.civilization_id}:raid:{report.day}"),
                recipient_civilization_id=enemy,
                traveller_ids=tuple(sorted(people[:RAID_BAND])),
                route=route,
                war_objective=WarObjective.RAID,
            )
        )
    return orders


TRAITS: dict[str, Callable[[CouncilReport], list[DirectOrder]]] = {
    "builder": lambda report: [],
    "expander": _expander,
    "trader": _trader,
    "raider": _raider,
}


def plan(policy: str, report: CouncilReport) -> tuple[Command, ...]:
    """The policy's orders for one council: its trait first, then the baseline's."""
    trait = TRAITS[policy](report)[:TRAIT_ORDERS]
    baseline: Sequence[Command] = plan_baseline_commands(report)
    return (*trait, *baseline)[:COUNCIL_ORDERS]


class CalibrationSovereign:
    """One civilization played by a calibration policy."""

    def __init__(self, policy: str) -> None:
        if policy not in TRAITS:
            raise ValueError(f"unknown policy {policy!r}; choose one of {', '.join(POLICIES)}")
        self.policy = policy

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=plan(self.policy, report),
        )
