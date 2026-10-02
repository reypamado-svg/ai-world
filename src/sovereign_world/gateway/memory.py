"""A sovereign's memory, rebuilt for every council from what its civilization holds.

There is no running conversation. Each council reads four layers:

1. the identity charter, fixed for a prompt version;
2. a summary of the civilization's state today, from its council report;
3. memories retrieved from what it has been told over the years: messages, battles,
   journeys, spies' findings, treaties. Those touching what matters now come first;
4. a short transcript of its own last councils: what it ordered and what came of it.

Every layer has a share of an equal context budget and is trimmed deterministically.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from sovereign_world.commands import CouncilReport
from sovereign_world.gateway.records import CouncilRecord
from sovereign_world.ids import EntityId

HISTORY_FIELDS = frozenset(
    {
        "received_messages",
        "logistics_notices",
        "war_reports",
        "spy_reports",
        "caught_spies",
        "recent_events",
    }
)
"""Report fields that grow with every year; they become memories, not state."""
DROPPED_FIELDS = frozenset({"known_tiles"})
"""Report fields the state layer repeats elsewhere (`known_terrain` lists the same tiles)."""
TRIMMED_LAST = (
    "known_terrain",
    "known_rivers",
    "observed_control",
    "known_roads",
    "known_tolls",
)
"""Map fields cut back, farthest from home first, when the state layer is too long."""


@dataclass(frozen=True, slots=True)
class Budgets:
    """The same for every sovereign in a run; frozen in its manifest."""

    state_chars: int = 36_000
    memory_chars: int = 16_000
    transcript_chars: int = 8_000
    transcript_turns: int = 3
    max_output_tokens: int = 8_000
    timeout_seconds: float = 300.0


@dataclass(frozen=True, slots=True)
class Memory:
    day: int
    kind: str
    about: frozenset[EntityId]
    text: str


def _compact(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _dump(item: BaseModel) -> dict[str, object]:
    dumped: dict[str, object] = item.model_dump(mode="json")
    return dumped


def _tile_distance(entry: object, home: tuple[int, int]) -> int:
    """Hex distance from home of a map entry, or a large number if it has no tile."""
    tile = entry
    if isinstance(entry, list) and entry:
        tile = entry[0]
    if isinstance(entry, dict):
        tile = entry.get("tile", entry)
    if isinstance(tile, dict) and "q" in tile and "r" in tile:
        dq, dr = int(tile["q"]) - home[0], int(tile["r"]) - home[1]
        return (abs(dq) + abs(dr) + abs(dq + dr)) // 2
    return 1_000_000


def state_summary(report: CouncilReport, budget: int) -> str:
    """Everything the council report says about today, without the years of history."""
    data = {
        key: value
        for key, value in report.model_dump(mode="json").items()
        if key not in HISTORY_FIELDS | DROPPED_FIELDS
    }
    home = (report.start_center.q, report.start_center.r)
    text = _compact(data)
    for field in TRIMMED_LAST:
        if len(text) <= budget:
            break
        entries = data.get(field)
        if not isinstance(entries, list):
            continue
        entries = sorted(entries, key=lambda entry: (_tile_distance(entry, home), _compact(entry)))
        while entries and len(text) > budget:
            entries.pop()
            data[field] = entries
            text = _compact(data)
    return text[:budget]


def _about(*ids: object) -> frozenset[EntityId]:
    return frozenset(EntityId(str(item)) for item in ids if item)


def memories(report: CouncilReport) -> list[Memory]:
    """Everything the civilization has been told over the years, as dated memories."""
    found: list[Memory] = []
    for message in report.received_messages:
        found.append(
            Memory(
                day=message.delivered_day or message.departed_day,
                kind="message",
                about=_about(message.sender_civilization_id),
                text=_compact(
                    {
                        "from": message.sender_civilization_id,
                        "words": message.delivered_text or "",
                        "treaty_offer": message.treaty_offer is not None,
                    }
                ),
            )
        )
    for battle in report.war_reports:
        found.append(Memory(battle.day, "battle", _about(battle.enemy_id), _compact(_dump(battle))))
    for notice in report.logistics_notices:
        found.append(
            Memory(
                notice.day,
                "journey",
                _about(notice.counterpart_civilization_id),
                _compact(_dump(notice)),
            )
        )
    for finding in report.spy_reports:
        found.append(
            Memory(
                finding.delivered_day,
                "spy_report",
                _about(finding.estimate.civilization_id),
                _compact(_dump(finding)),
            )
        )
    for caught in report.caught_spies:
        found.append(
            Memory(
                caught.day,
                "spy_caught",
                _about(caught.sender_civilization_id),
                _compact(_dump(caught)),
            )
        )
    return found


def _focus(report: CouncilReport) -> frozenset[EntityId]:
    """The civilizations that matter most today: enemies, partners, and those at the gates."""
    return _about(
        *(war.aggressor_id for war in report.wars),
        *(war.defender_id for war in report.wars),
        *(
            party
            for treaty in report.treaties
            if treaty.in_force
            for party in (treaty.proposer_civilization_id, treaty.recipient_civilization_id)
        ),
        *(journey.sender_civilization_id for journey in report.petitions),
        *(siege.besieger_id for siege in report.sieges),
    ) - {report.civilization_id}


def retrieved(report: CouncilReport, budget: int) -> str:
    """The memories most worth recalling today, oldest first, within the budget."""
    focus = _focus(report)

    def weight(memory: Memory) -> tuple[int, int, str]:
        relevant = 1 if memory.about & focus else 0
        return (-relevant, -memory.day, memory.text)

    chosen: list[Memory] = []
    used = 0
    for memory in sorted(memories(report), key=weight):
        line = f"day {memory.day} {memory.kind}: {memory.text}"
        if used + len(line) + 1 > budget:
            continue
        chosen.append(memory)
        used += len(line) + 1
    chosen.sort(key=lambda memory: (memory.day, memory.kind, memory.text))
    return "\n".join(f"day {memory.day} {memory.kind}: {memory.text}" for memory in chosen)


def transcript(records: Sequence[CouncilRecord], turns: int, budget: int) -> str:
    """The last few councils: what was ordered, why, and what became of the turn."""
    lines: list[str] = []
    for record in records[-turns:] if turns else ():
        lines.append(
            _compact(
                {
                    "day": record.day,
                    "outcome": record.outcome.value,
                    "commands": [_dump(command) for command in record.envelope.commands],
                    "rationale": record.envelope.rationale[:600],
                }
            )
        )
    while lines and len("\n".join(lines)) > budget:
        lines.pop(0)
    return "\n".join(lines)


def own_records(records: Iterable[CouncilRecord], civilization_id: EntityId) -> list[CouncilRecord]:
    return sorted(
        (record for record in records if record.civilization_id == civilization_id),
        key=lambda record: record.day,
    )
