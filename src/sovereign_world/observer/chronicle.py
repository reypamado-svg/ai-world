"""The chronicle: a day's events, each placed on the map where the record puts it (O3).

An event is placed by the first of these that finds a tile:

1. its own coordinates: `q`/`r` or `tile_q`/`tile_r` in its payload;
2. by id, in the world as saved that day: the payload's `settlement`, then its `battle`, then
   the event's subject, then its actor. A person is where they stood; a settlement, site,
   ruin, battle, garrison, toll post or occupation on its tile; a siege at its camp; a
   journey or expedition where its first traveller stood (else where its route had reached);
   a storehouse, institution or piece of work at its settlement or work site; a civilization
   at its capital (tagged so);
3. the same, in the world as saved the day before, for events that end something (a death,
   a return, a fall), whose subject may be gone by the evening;
4. otherwise the event is not placed.

When the world of the day a rule first looks at lacks the id, the other day is tried, and
the location says which day it came from. Nothing is guessed from an id's spelling except
`tile:q,r`, which is a tile.

Routine bookkeeping (the daily food, accepted orders, control of single tiles) is marked so
the observer can hide it by default.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sovereign_world.events import DomainEvent
from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.state import WorldState

ROUTINE = frozenset(
    {
        "food_consumed",
        "command_accepted",
        "control_gained",
        "control_lost",
        "tile_observed",
        "work_completed",
        "timber_gathered",
        "stone_gathered",
        "council_held",
    }
)
"""Bookkeeping, hidden by default: true and recorded, but not news."""
ENDING_SUFFIXES = (
    "_died",
    "_returned",
    "_arrived",
    "_ended",
    "_lifted",
    "_fell",
    "_abandoned",
    "_closed",
    "_disbanded",
    "_eliminated",
    "_destroyed",
    "_spent",
    "_exhausted",
    "_left_site",
)
"""Kinds that end something: placed first from the world of the day before."""
PAYLOAD_IDS = ("settlement", "battle")

TODAY = "that day"
DAY_BEFORE = "the day before"


def ends_something(kind: str) -> bool:
    return kind.endswith(ENDING_SUFFIXES)


@dataclass(frozen=True, slots=True)
class Place:
    q: int
    r: int
    basis: str
    """How it was found: "recorded" (the event's own tile), or the id it was found by."""
    as_of: str | None = None
    """For a place found by id: "that day" or "the day before"."""
    capital: bool = False
    """Found through a civilization, so placed at its capital."""

    def record(self) -> dict[str, Any]:
        out: dict[str, Any] = {"q": self.q, "r": self.r, "basis": self.basis}
        if self.as_of is not None:
            out["as_of"] = self.as_of
        if self.capital:
            out["capital"] = True
        return out


class PlaceIndex:
    """Where each entity of one saved day stands. Built once a day; people are looked up in
    their tables when asked."""

    def __init__(self, state: WorldState) -> None:
        self.state = state
        tiles: dict[str, HexCoord] = {}
        settlements: dict[str, HexCoord] = {}
        capitals: dict[str, HexCoord] = {}
        # Parties are placed by where their people stand, looked up only when asked:
        # (traveller ids, route, how far along it).
        parties: dict[str, tuple[tuple[str, ...], tuple[HexCoord, ...], int]] = {}
        homes: dict[str, str] = {}

        def add(entity_id: str | None, tile: HexCoord | None) -> None:
            if entity_id and tile is not None:
                tiles.setdefault(str(entity_id), tile)

        for civilization_id, civilization in state.civilizations.items():
            for settlement in civilization.settlements:
                settlements[str(settlement.settlement_id)] = settlement.tile
                add(settlement.settlement_id, settlement.tile)
                if settlement.capital:
                    capitals[str(civilization_id)] = settlement.tile
            for storehouse in civilization.storehouses:
                homes[str(storehouse.storehouse_id)] = str(storehouse.settlement_id)
            for job in civilization.storehouse_jobs:
                add(job.job_id, job.tile)
            for wall_job in civilization.wall_jobs:
                add(wall_job.job_id, wall_job.tile)
            for house_job in civilization.house_jobs:
                add(house_job.job_id, house_job.tile)
            for craft in civilization.craft_jobs:
                add(craft.job_id, craft.workshop)
            for project_id, project in civilization.projects.items():
                add(project_id, project.location)
            for institution in civilization.institutions:
                add(institution.institution_id, institution.tile)
            for garrison in civilization.garrisons:
                add(garrison.garrison_id, garrison.tile)
            for post in civilization.toll_posts:
                add(post.post_id, post.tile)
            for expedition in civilization.expeditions:
                parties[str(expedition.expedition_id)] = (
                    tuple(map(str, expedition.explorer_ids)),
                    expedition.route,
                    expedition.next_route_index,
                )
        for journey in state.journeys:
            parties[str(journey.journey_id)] = (
                tuple(map(str, journey.traveller_ids)),
                journey.route,
                journey.route_index,
            )
        for battle in state.battles:
            add(battle.battle_id, battle.tile)
        for siege in state.sieges:
            add(siege.siege_id, siege.camp)
        for occupation in state.occupations:
            add(occupation.occupation_id, occupation.tile)
        for site in state.sites:
            add(site.site_id, site.tile)
        for ruin in state.ruins:
            add(ruin.ruin_id, ruin.tile)
            settlements.setdefault(str(ruin.former_settlement_id), ruin.tile)
            add(ruin.former_settlement_id, ruin.tile)
        for storehouse_id, home in homes.items():
            add(storehouse_id, settlements.get(home))
        self._tiles = tiles
        self._parties = parties
        self._capitals = capitals

    def _person(self, person_id: str) -> HexCoord | None:
        found: HexCoord | None = None
        for civilization in self.state.civilizations.values():
            person = civilization.population.people.get(EntityId(person_id))
            if person is None:
                continue
            if person.alive:
                return HexCoord(person.location.q, person.location.r)
            found = found or HexCoord(person.location.q, person.location.r)
        return found

    def _first_of(self, person_ids: Iterable[str]) -> HexCoord | None:
        for person_id in person_ids:
            tile = self._person(person_id)
            if tile is not None:
                return tile
        return None

    def find(self, entity_id: str) -> tuple[HexCoord, bool] | None:
        """The tile an id stands on, and whether it was found as a civilization's capital."""
        if entity_id.startswith("tile:"):
            q, _, r = entity_id[len("tile:") :].partition(",")
            if q.lstrip("-").isdigit() and r.lstrip("-").isdigit():
                return HexCoord(int(q), int(r)), False
            return None
        if entity_id.startswith("person:"):
            tile = self._person(entity_id)
            return None if tile is None else (tile, False)
        if entity_id in self._capitals:
            return self._capitals[entity_id], True
        tile = self._tiles.get(entity_id)
        if tile is None and entity_id in self._parties:
            people, route, along = self._parties[entity_id]
            tile = self._first_of(people) or _along(route, along)
            if tile is not None:
                self._tiles[entity_id] = tile
        return None if tile is None else (tile, False)


def _along(route: tuple[HexCoord, ...], index: int) -> HexCoord | None:
    if not route:
        return None
    return route[min(index, len(route) - 1)]


def _own_tile(payload: dict[str, Any]) -> tuple[int, int] | None:
    for q_key, r_key in (("q", "r"), ("tile_q", "tile_r")):
        q, r = payload.get(q_key), payload.get(r_key)
        if isinstance(q, int) and isinstance(r, int) and not isinstance(q, bool):
            return q, r
    return None


def _ids(event: DomainEvent) -> list[str]:
    found = [
        str(event.payload[key]) for key in PAYLOAD_IDS if isinstance(event.payload.get(key), str)
    ]
    found += [entity_id for entity_id in (event.subject_id, event.actor_id) if entity_id]
    return found


def place(event: DomainEvent, today: PlaceIndex | None, before: PlaceIndex | None) -> Place | None:
    """Where an event happened, by the rules above; None when the record does not say."""
    own = _own_tile(event.payload)
    if own is not None:
        return Place(own[0], own[1], "recorded")
    days = [(TODAY, today), (DAY_BEFORE, before)]
    if ends_something(event.kind):
        days.reverse()
    for entity_id in _ids(event):
        for as_of, index in days:
            if index is None:
                continue
            found = index.find(entity_id)
            if found is not None:
                tile, capital = found
                return Place(tile.q, tile.r, entity_id, as_of, capital)
    return None


def chronicle_entries(
    events: Iterable[DomainEvent], today: WorldState, before: WorldState | None
) -> list[dict[str, Any]]:
    """A saved day's events in their saved order, each with its place (or none)."""
    now = PlaceIndex(today)
    earlier = PlaceIndex(before) if before is not None else None
    entries = []
    for event in events:
        spot = place(event, now, earlier)
        entries.append(
            {
                "day": event.day,
                "sequence": event.sequence,
                "phase": int(event.phase),
                "kind": event.kind,
                "actor_id": event.actor_id,
                "subject_id": event.subject_id,
                "payload": event.payload,
                "routine": event.kind in ROUTINE,
                "place": None if spot is None else spot.record(),
            }
        )
    return entries
