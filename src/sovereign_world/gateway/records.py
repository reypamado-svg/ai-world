"""Each council a model held, as recorded in the journal, and a sovereign that replays them."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from sovereign_world.commands import CommandEnvelope, CouncilReport
from sovereign_world.ids import EntityId
from sovereign_world.persistence import WorldStore

COUNCIL_RECORD = "council"
"""The journal record type for a council's prompt, replies and outcome."""


class CouncilOutcome(StrEnum):
    ACCEPTED = "accepted"
    REPAIRED = "repaired"
    """The first reply could not be read; the second could."""
    TIMEOUT = "no_commands:timeout"
    LATE = "no_commands:late"
    MALFORMED = "no_commands:malformed"
    REFUSED = "no_commands:refused"
    UNAVAILABLE = "no_commands:unavailable"


class ModelUsage(BaseModel):
    """One call to a model in a council: which model answered, and the tokens it took."""

    model_config = ConfigDict(frozen=True)

    purpose: str
    """``turn`` or ``repair``."""
    model: str
    """The model that answered, as the provider reported it (a server-side fallback may answer
    in place of the configured model)."""
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


class CouncilRecord(BaseModel):
    """One council turn: what was asked, every raw reply, and the envelope that resulted."""

    model_config = ConfigDict(frozen=True)

    civilization_id: EntityId
    day: int = Field(ge=0)
    report_id: str
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    replies: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    outcome: CouncilOutcome
    envelope: CommandEnvelope
    crisis_councils: bool = False
    """Whether this sovereign also holds councils when a crisis strikes."""
    usage: tuple[ModelUsage, ...] = ()
    """Each call's answering model and tokens (sealed trial: the spending cap is counted from
    these). Never shown to a model, never part of the world; left out of the record while
    empty, so councils without a model are saved as before."""

    @model_serializer(mode="wrap")
    def _omit_empty_usage(self, handler: SerializerFunctionWrapHandler) -> object:
        dumped = handler(self)
        if isinstance(dumped, dict) and not dumped.get("usage"):
            dumped.pop("usage", None)
        return dumped

    @property
    def issued(self) -> bool:
        return bool(self.envelope.commands)


class RecordedSovereign:
    """Gives each council the envelope recorded for it, and never calls a model."""

    def __init__(self, records: Iterable[CouncilRecord], *, crisis_councils: bool = False) -> None:
        self.crisis_councils = crisis_councils
        self._envelopes = {
            (record.civilization_id, record.day): record.envelope for record in records
        }

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        key = (report.civilization_id, report.day)
        if key not in self._envelopes:
            raise LookupError(f"no council was recorded for {key}")
        return self._envelopes[key]


RecordSink = Callable[[CouncilRecord], None]
"""Takes each council the moment it is held (the runner saves it to the journal at once)."""


class ResumeRefused(RuntimeError):
    """The run cannot be resumed as its journal stands."""


class ResumedCouncils:
    """The councils saved for the day a run was interrupted in, before the day itself was saved.

    Each is given back to its civilization when that day is held again, so its model is never
    asked twice; a council that does not match the one the world holds refuses the resume."""

    def __init__(self, records: Iterable[CouncilRecord] = ()) -> None:
        self._pending: dict[EntityId, CouncilRecord] = {}
        days = set()
        for record in records:
            days.add(record.day)
            if record.civilization_id in self._pending:
                raise ResumeRefused(
                    f"the journal holds two councils of {record.civilization_id} for day"
                    f" {record.day}"
                )
            self._pending[record.civilization_id] = record
        if len(days) > 1:
            raise ResumeRefused(
                f"the journal holds unfinished councils of days {sorted(days)}; only the day"
                " under way when the run stopped can have them"
            )
        self.day: int | None = days.pop() if days else None
        self.refused: str | None = None

    def take(self, civilization_id: EntityId) -> CouncilRecord | None:
        """The saved council of this civilization, once."""
        return self._pending.pop(civilization_id, None)

    def refuse(self, reason: str) -> None:
        if self.refused is None:
            self.refused = reason

    def pending(self) -> tuple[EntityId, ...]:
        return tuple(sorted(self._pending))


def split_resumed(
    records: Iterable[CouncilRecord], day: int
) -> tuple[tuple[CouncilRecord, ...], ResumedCouncils]:
    """The councils of days already saved, and those of `day`, the day under way when the run
    stopped (held before its day was saved). A council of a later day is corruption."""
    history: list[CouncilRecord] = []
    orphans: list[CouncilRecord] = []
    for record in records:
        if record.day < day:
            history.append(record)
        elif record.day == day:
            orphans.append(record)
        else:
            raise ResumeRefused(
                f"the journal holds a council of {record.civilization_id} for day {record.day},"
                f" but its last saved day is {day}"
            )
    return tuple(history), ResumedCouncils(orphans)


@runtime_checkable
class RecordsCouncils(Protocol):
    def drain_records(self) -> tuple[CouncilRecord, ...]: ...

    def record_to(self, sink: RecordSink | None) -> None: ...

    def resume_from(self, resumed: ResumedCouncils) -> None: ...


def journal_councils(
    store: WorldStore,
    sovereigns: Iterable[object],
    written: list[CouncilRecord] | None = None,
) -> int:
    """Append every council held since the last call to the journal; return how many (and
    add them to ``written`` when given)."""
    count = 0
    for sovereign in sovereigns:
        if not isinstance(sovereign, RecordsCouncils):
            continue
        for record in sovereign.drain_records():
            store.append_record(COUNCIL_RECORD, record.model_dump(mode="json"))
            if written is not None:
                written.append(record)
            count += 1
    return count


def recorded_councils(store: WorldStore) -> tuple[CouncilRecord, ...]:
    return tuple(
        CouncilRecord.model_validate(record.payload)
        for record in store.read_records()
        if record.type == COUNCIL_RECORD
    )
