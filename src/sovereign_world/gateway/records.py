"""Each council a model held, as recorded in the journal, and a sovereign that replays them."""

from __future__ import annotations

from collections.abc import Iterable
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


@runtime_checkable
class RecordsCouncils(Protocol):
    def drain_records(self) -> tuple[CouncilRecord, ...]: ...


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
