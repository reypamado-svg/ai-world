"""Shared fixtures for treaty-bound journey tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from sovereign_world.commands import CommandEnvelope, CouncilReport, DirectOrder
from sovereign_world.config import RunManifest, WorldConfig
from sovereign_world.diplomacy import ActiveTreaty, Contact, TreatyKind, TreatyOffer
from sovereign_world.exploration import Observation
from sovereign_world.hexmap import HexCoord, RiverEdge, Terrain, corner_tiles, edge_key
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import CivilizationState, WorldState, build_initial_state


def move_home(civilization: CivilizationState, tile: HexCoord) -> None:
    """Move a civilization's starting tile and its capital settlement together."""
    civilization.start_center = tile
    civilization.settlements = tuple(
        settlement.model_copy(update={"tile": tile}) if settlement.capital else settlement
        for settlement in civilization.settlements
    )


def linked_world(
    *,
    seed: int = 21,
    distance: int = 3,
    rules_version: int = 1,
) -> tuple[RunManifest, WorldState, EntityId, EntityId, tuple[HexCoord, ...]]:
    """Two civilizations in mutual contact, a short route both have observed.

    The world runs under rules version 1 unless asked otherwise, so scenarios test their own
    rules, not houses and ranks at a village-sized capital."""
    manifest = RunManifest.new(
        config=WorldConfig(seed=seed, width=48, height=48),
        engine_version="0.1.0",
        rules_version=rules_version,
    )
    state = build_initial_state(manifest)
    sender_id, recipient_id = sorted(state.civilizations)[:2]
    sender = state.civilizations[sender_id]
    recipient = state.civilizations[recipient_id]
    home = sender.start_center
    step = 1 if home.q + distance < state.config.width else -1
    route = tuple(HexCoord(home.q + index * step, home.r) for index in range(distance + 1))
    # Lay grassland along the route, with no rivers on its borders, so scenarios test their
    # own rules, not the terrain.
    grass = {tile: replace(state.world_map.tile(tile), terrain=Terrain.GRASSLAND) for tile in route}
    state.world_map = replace(
        state.world_map,
        tiles=tuple(grass.get(tile.coord, tile) for tile in state.world_map.tiles),
        rivers=tuple(
            edge for edge in state.world_map.rivers if edge.a not in grass and edge.b not in grass
        ),
    )
    move_home(recipient, route[-1])
    for person in recipient.population.people.values():
        person.location = route[-1]
    for civilization in (sender, recipient):
        observed = {observation.tile: observation for observation in civilization.observations}
        for tile in route:
            observed.setdefault(
                tile,
                Observation(
                    tile=tile,
                    observed_day=0,
                    observer_id=civilization.population.living_ids[0],
                ),
            )
        civilization.observations = tuple(sorted(observed.values(), key=lambda item: item.tile))
        civilization.known_tiles = tuple(item.tile for item in civilization.observations)
    sender.contacts = (
        Contact(
            civilization_id=recipient_id, settlement=route[-1], first_contact_day=0, last_seen_day=0
        ),
    )
    recipient.contacts = (
        Contact(civilization_id=sender_id, settlement=home, first_contact_day=0, last_seen_day=0),
    )
    return manifest, state, sender_id, recipient_id, route


def treaty_world(
    kind: TreatyKind | None = TreatyKind.TRADE,
    *,
    seed: int = 21,
    distance: int = 3,
    rules_version: int = 1,
) -> tuple[WorldState, EntityId, EntityId, tuple[HexCoord, ...]]:
    """Linked civilizations, optionally already bound by a ratified treaty."""
    _, state, sender_id, recipient_id, route = linked_world(
        seed=seed, distance=distance, rules_version=rules_version
    )
    if kind is not None:
        treaty_id = EntityId(f"treaty:{kind.value}")
        state.treaty_offers = (
            TreatyOffer(
                offer_id=treaty_id,
                proposer_civilization_id=sender_id,
                recipient_civilization_id=recipient_id,
                kind=kind,
                proposed_day=0,
            ),
        )
        state.active_treaties = (
            ActiveTreaty(
                treaty_id=treaty_id,
                proposer_civilization_id=sender_id,
                recipient_civilization_id=recipient_id,
                kind=kind,
                offered_day=0,
                activated_day=0,
            ),
        )
    return state, sender_id, recipient_id, route


def envelope(state: WorldState, civilization_id: EntityId, *orders: DirectOrder) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=civilization_id,
        council_day=state.day,
        correlation_id=f"test:{state.day}",
        commands=orders,
    )


class OneShotSovereign:
    """Issue fixed orders at the first council, then stay silent."""

    def __init__(self, *orders: DirectOrder) -> None:
        self.orders = orders
        self.spent = False

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        orders = () if self.spent else self.orders
        self.spent = True
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=orders,
        )


class ScheduledSovereign:
    """Issue fixed orders at the councils on the given days."""

    def __init__(self, orders_by_day: dict[int, tuple[DirectOrder, ...]]) -> None:
        self.orders_by_day = orders_by_day

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        return CommandEnvelope(
            schema_version=1,
            civilization_id=report.civilization_id,
            council_day=report.day,
            correlation_id=report.report_id,
            commands=self.orders_by_day.get(report.day, ()),
        )


def roll_matching_id(
    prefix: str,
    stream: str,
    predicate: Callable[[int], bool],
    *,
    start_day: int = 0,
    days: int = 1,
    seed: int = 21,
) -> str:
    """Find an ID whose named daily travel rolls all satisfy the predicate.

    `stream` is "logistics" for journeys or "diplomacy" for ambassador messages.
    """
    rng = StableRng(seed)
    for index in range(100_000):
        candidate = f"{prefix}-{index}"
        if all(
            predicate(int(rng.stream(f"day:{day}:{stream}:travel:{candidate}").integers(0, 10_000)))
            for day in range(start_day, start_day + days)
        ):
            return candidate
    raise AssertionError("no matching roll found")


def clear_journey_id(prefix: str, *, start_day: int = 0, days: int = 8) -> str:
    """A journey ID that moves, without delay or hazard, on every day in the window."""
    return roll_matching_id(
        f"journey:{prefix}",
        "logistics",
        lambda roll: roll >= 900,
        start_day=start_day,
        days=days,
    )


def clear_message_id(prefix: str, *, start_day: int = 0, days: int = 8) -> str:
    """A message ID whose ambassador moves, without delay or loss, on every day in the window."""
    return roll_matching_id(
        f"message:{prefix}",
        "diplomacy",
        lambda roll: roll >= 900,
        start_day=start_day,
        days=days,
    )


def flatten(state: WorldState, around: tuple[HexCoord, ...], reach: int = 2) -> None:
    """Grassland within reach of the given tiles, and no rivers anywhere."""
    near = {
        tile.coord
        for tile in state.world_map.tiles
        if min(tile.coord.distance(spot) for spot in around) <= reach
    }
    state.world_map = replace(
        state.world_map,
        tiles=tuple(
            replace(tile, terrain=Terrain.GRASSLAND) if tile.coord in near else tile
            for tile in state.world_map.tiles
        ),
        rivers=(),
    )


def river(state: WorldState, first: HexCoord, second: HexCoord, flow: int) -> None:
    a, b = edge_key(first, second)
    edge = RiverEdge(a=a, b=b, flow=flow, downstream=corner_tiles(a, b)[0])
    state.world_map = replace(
        state.world_map, rivers=tuple(sorted((*state.world_map.rivers, edge)))
    )
