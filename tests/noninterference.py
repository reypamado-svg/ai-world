"""No hidden knowledge: a council report must not change when anything its civilization has
not seen or been told changes.

`hide_unseen` rewrites a copy of the world so that everything one civilization could not
know is different or gone, and leaves what it may know alone. A report built from that
copy must be identical to the real one.
"""

from __future__ import annotations

from collections.abc import Iterable

from scenario_helpers import Council

from sovereign_world.commands import build_council_report, sight_of
from sovereign_world.diplomacy import TreatyEndKind
from sovereign_world.ids import EntityId
from sovereign_world.resources import Inventory
from sovereign_world.state import WorldState


def _other_ending(kind: TreatyEndKind | None) -> TreatyEndKind:
    return TreatyEndKind.CANCELLED if kind is TreatyEndKind.BREACHED else TreatyEndKind.BREACHED


def hide_unseen(state: WorldState, civilization_id: EntityId) -> WorldState:
    """A copy of the world with everything this civilization cannot know changed."""
    world = state.model_copy(deep=True)
    own = world.civilizations[civilization_id]
    in_sight = sight_of(world, civilization_id)
    # Its own people it has not been told are prisoners look, to it, simply away.
    known_captives = set(own.known_captives)
    for person_id, person in own.population.people.items():
        if person.captive_of is not None and person_id not in known_captives:
            person.captive_of = None
            person.held_at = None

    for other_id, other in world.civilizations.items():
        if other_id == civilization_id:
            continue
        for person in other.population.people.values():
            if person.captive_of == civilization_id and person.alive:
                continue  # Prisoners it holds are in its hands.
            person.skills = {}
            person.held_skills = {}
            person.languages = {}
            person.culture = None
            person.assimilation = 0
            person.ancestry = ()
            person.health_bp = 1 if person.alive else 0
            person.nutrition_debt = 0
        other.inventory = Inventory(capacity=other.inventory.capacity)
        other.stores = {}
        other.storehouses = ()
        other.storehouse_jobs = ()
        other.walls = ()
        other.wall_jobs = ()
        other.homeless_since = None
        other.work_orders = ()
        other.projects = {}
        other.known_tiles = ()
        other.observations = ()
        other.expeditions = ()
        other.capabilities = ()
        other.teaching_assignments = ()
        other.contacts = ()
        other.received_messages = ()
        other.logistics_notices = ()
        other.spy_reports = ()
        other.caught_spies = ()
        other.institutions = ()
        other.ruin_intel = ()
        other.fallen = {}
        other.known_captives = ()
        other.garrisons = ()
        other.claims = ()
        other.road_intel = ()
        other.toll_intel = ()
        other.drills = ()
        other.craft_jobs = ()
        other.research = ()
        other.research_points = {}
        other.war_reports = ()
        other.toll_posts = tuple(
            post
            if post.tile in in_sight
            else post.model_copy(
                update={
                    "cargo_rate_bp": 0 if post.cargo_rate_bp else 1,
                    "food_per_head": 0 if post.food_per_head else 1,
                }
            )
            for post in other.toll_posts
        )

    world.active_decrees = {
        key: value for key, value in world.active_decrees.items() if key == civilization_id
    }
    world.journeys = tuple(
        journey
        for journey in world.journeys
        if journey.sender_civilization_id == civilization_id
        or (journey.waiting and journey.recipient_civilization_id == civilization_id)
    )
    world.diplomatic_missions = tuple(
        message
        for message in world.diplomatic_missions
        if message.sender_civilization_id == civilization_id
    )
    world.treaty_offers = tuple(
        offer
        for offer in world.treaty_offers
        if civilization_id in {offer.proposer_civilization_id, offer.recipient_civilization_id}
    )
    treaties = []
    for treaty in world.active_treaties:
        parties = {treaty.proposer_civilization_id, treaty.recipient_civilization_id}
        if civilization_id not in parties:
            continue
        if (
            treaty.ended_by is not None
            and treaty.ended_by != civilization_id
            and treaty.notice_day is None
        ):
            # Ended by the other side and not yet heard of: any ending looks the same.
            treaty = treaty.model_copy(update={"end_kind": _other_ending(treaty.end_kind)})
        treaties.append(treaty)
    world.active_treaties = tuple(treaties)
    world.battles = ()
    world.wars = tuple(
        war
        for war in world.wars
        if civilization_id in {war.aggressor_id, war.defender_id} and war.known_to(civilization_id)
    )
    world.sieges = tuple(
        siege
        for siege in world.sieges
        if siege.besieger_id == civilization_id
        or (siege.defender_id == civilization_id and siege.defender_learned_day is not None)
    )
    world.occupations = tuple(
        occupation
        for occupation in world.occupations
        if occupation.occupier_id == civilization_id
        or (occupation.owner_id == civilization_id and occupation.owner_learned_day is not None)
    )
    world.ruins = tuple(ruin for ruin in world.ruins if ruin.tile in in_sight)
    world.endings = ()
    return world


def assert_no_hidden_knowledge(councils: Iterable[Council]) -> int:
    """Every report is rebuilt from its state, then from the state with the unseen hidden.

    Returns how many reports were checked.
    """
    checked = 0
    for council in councils:
        civilization_id = council.civilization_id
        assert build_council_report(council.state, civilization_id) == council.report
        hidden = build_council_report(hide_unseen(council.state, civilization_id), civilization_id)
        if hidden != council.report:
            differing = [
                name
                for name in type(council.report).model_fields
                if getattr(hidden, name) != getattr(council.report, name)
            ]
            raise AssertionError(
                f"day {council.report.day}: {civilization_id}'s report changes when hidden "
                f"things change: {differing}"
            )
        checked += 1
    return checked
