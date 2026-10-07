"""One scripted history for balance calibration: a world built and played to its end, with
one row of outcomes per civilization.

No store is written: the engine's days are advanced in memory, and the world is checked every
30 days. The same spec always gives the same rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

from sovereign_world.calibration.policies import CalibrationSovereign
from sovereign_world.config import ENGINE_VERSION, RunManifest, WorldConfig
from sovereign_world.engine import advance_day
from sovereign_world.ids import EntityId
from sovereign_world.rng import StableRng
from sovereign_world.state import WorldState, build_initial_state, validate_world

CHECK_EVERY = 30
COUNTED = ("person_born", "person_died", "war_declared", "battle_joined", "command_rejected")
"""Events counted per civilization (its actor)."""


@dataclass(frozen=True)
class HistorySpec:
    """What to play: a world, a start rotation, and which policy each civilization plays (by
    its number, which with rotation r holds the generator's ``(i + r) % n``-th start)."""

    seed: int
    size: int
    days: int
    rotation: int
    assignment: tuple[str, ...]
    """The policy of civilization 0, 1, 2, ... in order."""
    label: str = ""
    """The assignment's name in reports (``builders``, ``mixed``)."""
    interval: int = 30
    """Days between councils."""

    @property
    def key(self) -> str:
        """Names the history: rows already written under it are not played again."""
        key = f"{self.label}|{self.seed}|{self.size}|{self.days}|{self.rotation}"
        # Histories played before the interval could be chosen are monthly, and keep their key.
        return key if self.interval == 30 else f"{key}|{self.interval}"


FIELDS = (
    "assignment",
    "seed",
    "size",
    "days",
    "rotation",
    "civilization",
    "position",
    "policy",
    "living",
    "peak",
    "births",
    "deaths",
    "settlements",
    "tiles",
    "realm_rank",
    "wars_declared",
    "battles",
    "eliminated_day",
    "homeless",
    "rejected_orders",
    "winner_share",
)
"""The columns of a history's rows, in order."""


@dataclass(frozen=True)
class Row:
    assignment: str
    seed: int
    size: int
    days: int
    rotation: int
    civilization: int
    """Its number, by id (0001 is 0)."""
    position: int
    """Which of the generator's starts it held: ``(civilization + rotation) % n``."""
    policy: str
    living: int
    peak: int
    births: int
    deaths: int
    settlements: int
    tiles: int
    realm_rank: str
    wars_declared: int
    battles: int
    eliminated_day: int | None
    homeless: bool
    rejected_orders: int
    winner_share: float
    """1 for the civilization with the most living people at the end, shared when tied."""

    def values(self) -> dict[str, object]:
        return asdict(self)


def run_history(spec: HistorySpec) -> list[Row]:
    """Play one history and return a row per civilization."""
    manifest = RunManifest.model_validate(
        {
            **RunManifest.new(
                WorldConfig(
                    seed=spec.seed,
                    width=spec.size,
                    height=spec.size,
                    civilizations=len(spec.assignment),
                    council_interval_days=spec.interval,
                ),
                ENGINE_VERSION,
            ).model_dump(),
            "start_rotation": spec.rotation,
        }
    )
    state = build_initial_state(manifest)
    ids = sorted(state.civilizations)
    sovereigns = {
        civilization_id: CalibrationSovereign(spec.assignment[index])
        for index, civilization_id in enumerate(ids)
    }
    rng = StableRng(manifest.config.seed)
    counts: dict[str, Counter[EntityId]] = {
        kind: Counter()
        for kind in (
            "person_born",
            "person_died",
            "war_declared",
            "battle_joined",
            "command_rejected",
        )
    }
    eliminated: dict[EntityId, int] = {}
    peak = {civilization_id: _living(state, civilization_id) for civilization_id in ids}
    for _ in range(spec.days):
        transition = advance_day(state, rng, sovereigns=sovereigns)
        state = transition.state
        for event in transition.events.events:
            actor = EntityId(event.actor_id) if event.actor_id else None
            if actor is None:
                continue
            if event.kind in counts:
                counts[event.kind][actor] += 1
            elif event.kind == "civilization_eliminated":
                eliminated.setdefault(actor, state.day)
        for civilization_id in ids:
            peak[civilization_id] = max(peak[civilization_id], _living(state, civilization_id))
        if state.day % CHECK_EVERY == 0:
            validate_world(state)
    validate_world(state)
    owners = state.territory.owner_of()
    tiles = Counter(owners.values())
    living = {civilization_id: _living(state, civilization_id) for civilization_id in ids}
    most = max(living.values())
    leaders = [civilization_id for civilization_id in ids if living[civilization_id] == most]
    rows = []
    for index, civilization_id in enumerate(ids):
        civilization = state.civilizations.get(civilization_id)
        rows.append(
            Row(
                assignment=spec.label,
                seed=spec.seed,
                size=spec.size,
                days=spec.days,
                rotation=spec.rotation,
                civilization=index,
                position=(index + spec.rotation) % len(ids),
                policy=spec.assignment[index],
                living=living[civilization_id],
                peak=peak[civilization_id],
                births=counts["person_born"][civilization_id],
                deaths=counts["person_died"][civilization_id],
                settlements=len(civilization.settlements) if civilization else 0,
                tiles=tiles[civilization_id],
                realm_rank=str(civilization.realm_rank_reached) if civilization else "",
                wars_declared=counts["war_declared"][civilization_id],
                battles=counts["battle_joined"][civilization_id],
                eliminated_day=eliminated.get(civilization_id),
                homeless=bool(civilization and civilization.homeless_since is not None),
                rejected_orders=counts["command_rejected"][civilization_id],
                winner_share=(1 / len(leaders)) if civilization_id in leaders and most > 0 else 0.0,
            )
        )
    return rows


def _living(state: WorldState, civilization_id: EntityId) -> int:
    civilization = state.civilizations.get(civilization_id)
    if civilization is None:
        return 0
    return len(civilization.population.living_ids)
