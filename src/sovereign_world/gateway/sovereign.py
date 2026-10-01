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
from sovereign_world.gateway.records import CouncilOutcome, CouncilRecord
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

    def remember(self, records: Iterable[CouncilRecord]) -> None:
        """Take up the councils already held, e.g. from the journal of a resumed run."""
        self._history = list(records)

    def drain_records(self) -> tuple[CouncilRecord, ...]:
        """The councils held since the last call, for the journal."""
        records, self._records = tuple(self._records), []
        return records

    def _ask(self, system: str, user: str, purpose: str, replies: list[str]) -> str:
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
        return reply.text

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        history = own_records(self._history, report.civilization_id)
        system, user = self._build(report, history, self.budgets)
        replies: list[str] = []
        errors: list[str] = []
        started = self._clock()
        envelope = no_commands(report)
        try:
            text = self._ask(system, user, "turn", replies)
            try:
                envelope = to_envelope(parse_reply(text), report)
                outcome = CouncilOutcome.ACCEPTED
            except ReplyError as error:
                errors.append(str(error))
                repaired = self._ask(
                    system, f"{user}\n\n{repair_note(error, text)}", "repair", replies
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
        )
        self._records.append(record)
        self._history.append(record)
        return envelope


class RecordingSovereign:
    """Records the councils of a sovereign that needs no model, such as a scripted one."""

    def __init__(self, inner: Sovereign, name: str = "scripted") -> None:
        self.inner = inner
        self.name = name
        self.crisis_councils = bool(getattr(inner, "crisis_councils", False))
        self._records: list[CouncilRecord] = []

    def drain_records(self) -> tuple[CouncilRecord, ...]:
        records, self._records = tuple(self._records), []
        return records

    def decide(self, report: CouncilReport) -> CommandEnvelope:
        envelope = self.inner.decide(report)
        self._records.append(
            CouncilRecord(
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
        )
        return envelope
