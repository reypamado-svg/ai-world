"""A sovereign played by a model, through the gateway.

It asks the model once and, if the reply cannot be read, once more. Whatever goes wrong,
it never raises into the engine and never gives the civilization more than a council's
envelope: at worst an empty one, so its standing decrees and works carry on.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable, Sequence

from sovereign_world.commands import CommandEnvelope, CouncilReport
from sovereign_world.gateway.envelope import (
    ReplyError,
    no_commands,
    parse_reply,
    repair_note,
    to_envelope,
)
from sovereign_world.gateway.memory import Budgets, own_records
from sovereign_world.gateway.prompt import PROMPT_VERSION, build_prompt
from sovereign_world.gateway.provider import (
    ModelProvider,
    ModelRequest,
    ProviderError,
    ProviderRefused,
    ProviderTimeout,
)
from sovereign_world.gateway.records import (
    CouncilOutcome,
    CouncilRecord,
    ModelUsage,
    RecordSink,
    ResumedCouncils,
    ResumeRefused,
)
from sovereign_world.scripted import Sovereign

PromptBuilder = Callable[[CouncilReport, Sequence[CouncilRecord], Budgets], tuple[str, str]]

FAILURES: dict[type[ProviderError], CouncilOutcome] = {
    ProviderTimeout: CouncilOutcome.TIMEOUT,
    ProviderRefused: CouncilOutcome.REFUSED,
}


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256(f"{system}\0{user}".encode()).hexdigest()


class GatewaySovereign:
    """Plays a civilization's councils with a model, and records every turn."""

    crisis_councils = True
    """A model-played sovereign is also called to council when a crisis strikes."""

    def __init__(
        self,
        provider: ModelProvider,
        *,
        prompt_version: str = PROMPT_VERSION,
        build: PromptBuilder = build_prompt,
        budgets: Budgets | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.provider = provider
        self.prompt_version = prompt_version
        self._build = build
        self.budgets = budgets or Budgets()
        self._clock = clock
        self._records: list[CouncilRecord] = []
        self._history: list[CouncilRecord] = []
        self._sink: RecordSink | None = None
        self._resumed: ResumedCouncils | None = None

    def record_to(self, sink: RecordSink | None) -> None:
        """Hand each council to `sink` the moment it is held, instead of keeping it to drain."""
        self._sink = sink

    def resume_from(self, resumed: ResumedCouncils) -> None:
        """Councils saved for the day under way when the run stopped: reused, never re-asked."""
        self._resumed = resumed

    def remember(self, records: Iterable[CouncilRecord]) -> None:
        """Take up the councils already held, e.g. from the journal of a resumed run."""
        self._history = list(records)

    def drain_records(self) -> tuple[CouncilRecord, ...]:
        """The councils held since the last call, for the journal."""
        records, self._records = tuple(self._records), []
        return records

    def _ask(
        self,
        system: str,
        user: str,
        purpose: str,
        replies: list[str],
        usage: list[ModelUsage] | None = None,
    ) -> str:
        reply = self.provider.complete(
            ModelRequest(
                system=system,
                user=user,
                max_output_tokens=self.budgets.max_output_tokens,
                timeout_seconds=self.budgets.timeout_seconds,
                purpose=purpose,
            )
        )
        replies.append(reply.text)
        if usage is not None:
            usage.append(
                ModelUsage(
                    purpose=purpose,
                    model=reply.model or self.provider.model,
                    input_tokens=max(0, reply.input_tokens),
                    output_tokens=max(0, reply.output_tokens),
                )
            )
        return reply.text

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        history = own_records(self._history, report.civilization_id)
        system, user = self._build(report, history, self.budgets)
        resumed = self._resumed
        if resumed is not None:
            orphan = _saved_council(resumed, report)
            if orphan is not None:
                if orphan.prompt_hash != prompt_hash(system, user):
                    resumed.refuse(
                        f"the saved council of {report.civilization_id} for day {report.day} was"
                        " asked a different question than the world asks today"
                    )
                    raise ResumeRefused(resumed.refused)
                self._history.append(orphan)
                return orphan.envelope
        replies: list[str] = []
        errors: list[str] = []
        usage: list[ModelUsage] = []
        started = self._clock()
        envelope = no_commands(report)
        try:
            text = self._ask(system, user, "turn", replies, usage)
            try:
                envelope = to_envelope(parse_reply(text), report)
                outcome = CouncilOutcome.ACCEPTED
            except ReplyError as error:
                errors.append(str(error))
                repaired = self._ask(
                    system, f"{user}\n\n{repair_note(error, text)}", "repair", replies, usage
                )
                envelope = to_envelope(parse_reply(repaired), report)
                outcome = CouncilOutcome.REPAIRED
        except ReplyError as error:
            errors.append(str(error))
            envelope, outcome = no_commands(report), CouncilOutcome.MALFORMED
        except ProviderError as error:
            errors.append(f"{type(error).__name__}: {error}")
            envelope = no_commands(report)
            outcome = FAILURES.get(type(error), CouncilOutcome.UNAVAILABLE)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")
            envelope, outcome = no_commands(report), CouncilOutcome.UNAVAILABLE
        if outcome in {CouncilOutcome.ACCEPTED, CouncilOutcome.REPAIRED} and (
            self._clock() - started > self.budgets.timeout_seconds
        ):
            # A reply that came after the council rose is not acted on.
            errors.append("the reply came after the turn's time ran out")
            envelope, outcome = no_commands(report), CouncilOutcome.LATE
        record = CouncilRecord(
            civilization_id=report.civilization_id,
            day=report.day,
            report_id=report.report_id,
            provider=self.provider.name,
            model=self.provider.model,
            prompt_version=self.prompt_version,
            prompt_hash=prompt_hash(system, user),
            replies=tuple(replies),
            errors=tuple(errors),
            outcome=outcome,
            envelope=envelope,
            crisis_councils=self.crisis_councils,
            usage=tuple(usage),
        )
        self._history.append(record)
        self._emit(record)
        return envelope

    def _emit(self, record: CouncilRecord) -> None:
        if self._sink is not None:
            self._sink(record)
        else:
            self._records.append(record)


class RecordingSovereign:
    """Records the councils of a sovereign that needs no model, such as a scripted one."""

    def __init__(self, inner: Sovereign, name: str = "scripted") -> None:
        self.inner = inner
        self.name = name
        self.crisis_councils = bool(getattr(inner, "crisis_councils", False))
        self._records: list[CouncilRecord] = []
        self._sink: RecordSink | None = None
        self._resumed: ResumedCouncils | None = None

    def record_to(self, sink: RecordSink | None) -> None:
        self._sink = sink

    def resume_from(self, resumed: ResumedCouncils) -> None:
        self._resumed = resumed

    def drain_records(self) -> tuple[CouncilRecord, ...]:
        records, self._records = tuple(self._records), []
        return records

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        resumed = self._resumed
        orphan = _saved_council(resumed, report) if resumed is not None else None
        envelope = self.inner.decide(report)
        if orphan is not None and resumed is not None:
            if orphan.envelope != envelope:
                resumed.refuse(
                    f"the saved council of {report.civilization_id} for day {report.day} gave"
                    " other orders than it gives today"
                )
                raise ResumeRefused(resumed.refused)
            return envelope
        record = CouncilRecord(
            civilization_id=report.civilization_id,
            day=report.day,
            report_id=report.report_id,
            provider=self.name,
            model=type(self.inner).__name__,
            prompt_version="none",
            prompt_hash="",
            outcome=CouncilOutcome.ACCEPTED,
            envelope=envelope,
            crisis_councils=self.crisis_councils,
        )
        if self._sink is not None:
            self._sink(record)
        else:
            self._records.append(record)
        return envelope


def _saved_council(resumed: ResumedCouncils, report: CouncilReport) -> CouncilRecord | None:
    """This council's saved record from an interrupted day, checked against the report; a
    refused resume asks no model and saves nothing."""
    if resumed.refused is not None:
        raise ResumeRefused(resumed.refused)
    orphan = resumed.take(report.civilization_id)
    if orphan is not None and orphan.report_id != report.report_id:
        resumed.refuse(
            f"the saved council of {report.civilization_id} answered {orphan.report_id}, but"
            f" the world holds {report.report_id}"
        )
        raise ResumeRefused(resumed.refused)
    return orphan
