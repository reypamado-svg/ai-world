"""Trade partners join their roads and pass free; once the treaty breaks, the toll is paid.

Migrants sent on the day the treaty breaks packed for free passage, so they cannot pay and
turn back; the next party, planned after the break, packs for the toll and pays it.
"""

from pathlib import Path

from logistics_helpers import ScheduledSovereign, linked_world

from sovereign_world.commands import DirectOrder, DirectOrderKind, build_council_report
from sovereign_world.diplomacy import ActiveTreaty, TreatyKind, TreatyOffer
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.resources import Resource
from sovereign_world.rng import StableRng
from sovereign_world.roads import Road, RoadGrade
from sovereign_world.state import state_hash, validate_world
from sovereign_world.territory import Garrison, HeldControl, Territory, TileOwner
from sovereign_world.tolls import TollPost

DAYS = 90


def test_joined_roads_pass_free_until_the_treaty_breaks(tmp_path: Path) -> None:
    manifest, state, home, rival, route = linked_world(distance=6)
    state.treaty_offers = tuple(
        TreatyOffer(
            offer_id=EntityId(f"treaty:{kind.value}"),
            proposer_civilization_id=home,
            recipient_civilization_id=rival,
            kind=kind,
            proposed_day=0,
        )
        for kind in (TreatyKind.MIGRATION, TreatyKind.TRADE)
    )
    state.active_treaties = tuple(
        ActiveTreaty(
            treaty_id=offer.offer_id,
            proposer_civilization_id=home,
            recipient_civilization_id=rival,
            kind=offer.kind,
            offered_day=0,
            activated_day=0,
        )
        for offer in state.treaty_offers
    )
    post_tile = route[5]
    keepers = state.civilizations[rival]
    members = tuple(sorted(keepers.population.living_ids[12:15]))
    for person_id in members:
        keepers.population.people[person_id].location = post_tile
    keepers.garrisons = (
        Garrison(
            garrison_id=EntityId("garrison:gate"),
            civilization_id=rival,
            tile=post_tile,
            member_ids=members,
            since_day=0,
        ),
    )
    keepers.toll_posts = (
        TollPost(
            post_id=EntityId("toll:gate"),
            civilization_id=rival,
            tile=post_tile,
            cargo_rate_bp=1_000,
            food_per_head=2,
            deposit_every_days=10,
            deposit_route=(post_tile, route[6]),
            set_day=0,
            last_deposit_day=0,
        ),
    )
    state.roads = (
        Road(
            tile=post_tile, grade=RoadGrade.TRACK, civilization_id=rival, built_day=0, graded_day=0
        ),
    )
    state.territory = Territory(
        held=(HeldControl(tile=post_tile, civilization_id=rival, value=90),),
        owners=(TileOwner(tile=post_tile, civilization_id=rival, since_day=0),),
    )

    def people(civilization_id, window: slice):
        return tuple(state.civilizations[civilization_id].population.living_ids[window])

    def road(civilization_id, path, day):
        return DirectOrder(
            command_id=f"road:{day}",
            kind=DirectOrderKind.BUILD_ROAD,
            journey_id=EntityId(f"journey:{civilization_id}:road:{day}"),
            traveller_ids=people(civilization_id, slice(0, 8)),
            route=path,
            road_grade=RoadGrade.FOOTPATH,
        )

    def migrants(day):
        return DirectOrder(
            command_id=f"migrate:{day}",
            kind=DirectOrderKind.DISPATCH_MIGRATION,
            journey_id=EntityId(f"journey:migrate:{day}"),
            treaty_id=EntityId("treaty:migration"),
            recipient_civilization_id=rival,
            traveller_ids=people(home, slice(-2 - day // 15, None))[:2],
            # Day 0: the last two people; day 30: the two before; day 60: two more.
            route=route,
        )

    sovereigns = {
        home: ScheduledSovereign(
            {0: (road(home, route[:4], 0), migrants(0)), 30: (migrants(30),), 60: (migrants(60),)}
        ),
        rival: ScheduledSovereign(
            {
                0: (road(rival, tuple(reversed(route[3:])), 0),),
                30: (
                    DirectOrder(
                        command_id="break",
                        kind=DirectOrderKind.REPUDIATE_TREATY,
                        treaty_id=EntityId("treaty:trade"),
                    ),
                ),
            }
        ),
    }

    def run(store: WorldStore | None = None):
        current = state.model_copy(deep=True)
        rng = StableRng(manifest.config.seed)
        events: list[DomainEvent] = []
        for _ in range(DAYS):
            transition = advance_day(current, rng, sovereigns=sovereigns)
            current = transition.state
            if store is not None:
                store.append_transition(current, transition.events)
            events.extend(transition.events.events)
        return current, events

    store = WorldStore.create(tmp_path / "record", manifest, state)
    final, events = run(store)

    def of(kind: str) -> list[DomainEvent]:
        return [event for event in events if event.kind == kind]

    [joined] = of("roads_joined")
    assert joined.day < 30, "both crews built toward each other and the roads met"
    assert any(event.day < 30 for event in of("migrants_received"))
    assert all(event.day >= 30 for event in of("toll_paid")), "partners passed the post free"
    [refused] = of("toll_refused")
    assert refused.subject_id == "journey:migrate:30", "the break caught this party unprepared"
    turned = next(item for item in final.journeys if item.journey_id == refused.subject_id)
    assert turned.outcome.value == "turned_back" and not turned.active
    [paid] = of("toll_paid")
    assert paid.subject_id == "journey:migrate:60", "the next party packed for the toll"
    assert paid.payload["units"] == 4, "two migrants, two food each, once the treaty broke"
    [collected] = of("toll_collected")
    assert collected.day == paid.day
    dispatched = of("toll_deposit_dispatched")
    deposited = of("toll_deposited")
    assert dispatched and deposited
    assert dispatched[0].day >= paid.day and deposited[0].day > dispatched[0].day, (
        "the takings reached the store only when couriers carried them there"
    )
    assert deposited[0].payload["units"] == 4
    report = build_council_report(final, home)
    assert any(view.tile == post_tile for view in report.known_tolls)
    notices = {notice.kind.value for notice in final.civilizations[rival].logistics_notices}
    assert {"toll_collected", "toll_deposited"} <= notices
    assert final.civilizations[rival].toll_posts[0].chest.get(Resource.FOOD, 0) == 0

    validate_world(final)
    assert state_hash(replay_run(store)) == state_hash(final)
    assert verify_run(store).state_hash == state_hash(final)
    rerun, _ = run()
    assert state_hash(rerun) == state_hash(final)
