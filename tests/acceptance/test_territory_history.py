"""Two capitals close enough for their land to meet form a contested frontier, replayably."""

from pathlib import Path

from logistics_helpers import linked_world

from sovereign_world.commands import build_council_report
from sovereign_world.engine import advance_day
from sovereign_world.events import DomainEvent
from sovereign_world.persistence import WorldStore
from sovereign_world.replay import replay_run, verify_run
from sovereign_world.rng import StableRng
from sovereign_world.state import state_hash, validate_world


def test_neighbouring_capitals_meet_at_a_contested_frontier(tmp_path: Path) -> None:
    manifest, state, first, second, route = linked_world(distance=7)
    store = WorldStore.create(tmp_path / "record", manifest, state)
    rng = StableRng(manifest.config.seed)
    events: list[DomainEvent] = []
    for _ in range(40):
        transition = advance_day(state, rng)
        state = transition.state
        store.append_transition(state, transition.events)
        events.extend(transition.events.events)

    owners = state.territory.owner_of()
    assert owners[route[0]] == first
    assert owners[route[-1]] == second
    assert owners[route[1]] == first, "each capital holds the land beside it"
    assert owners[route[-2]] == second
    middle = route[len(route) // 2]
    assert middle in state.territory.contested(), "both reach the middle of the road"
    assert {event.kind for event in events} >= {"control_gained"}
    assert not any(event.kind == "control_lost" for event in events), "no flicker"

    first_report = build_council_report(state, first)
    assert set(first_report.controlled_tiles) == {
        tile for tile, owner in owners.items() if owner == first
    }
    validate_world(state)
    assert state_hash(replay_run(store)) == state_hash(state)
    assert verify_run(store).state_hash == state_hash(state)
