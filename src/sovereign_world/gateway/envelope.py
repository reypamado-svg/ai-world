"""What a model may write, and how its reply becomes a command envelope.

The model writes only its commands and its reasoning. The gateway fills in which
civilization and which council the envelope belongs to, so a model cannot forge them.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sovereign_world.commands import Command, CommandEnvelope, CouncilReport

REPLY_SCHEMA_VERSION = 1
MAX_REPLY_BYTES = 64_000
"""Replies larger than this are refused before they are read."""
COMMAND_ALLOWANCE = 8
"""Commands each council may issue, the same for every sovereign."""
MAX_RATIONALE = 4_000


class SovereignReply(BaseModel):
    """A sovereign's answer to its council: what to do, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    commands: tuple[Command, ...] = Field(default=(), max_length=COMMAND_ALLOWANCE)
    rationale: str = Field(default="", max_length=MAX_RATIONALE)


class ReplyError(ValueError):
    """A reply that cannot be read as a sovereign's answer."""


def reply_schema() -> dict[str, object]:
    return SovereignReply.model_json_schema()


def _json_object(text: str) -> object:
    """The JSON object in a reply, allowing for a fenced block or words around it."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ReplyError("the reply holds no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise ReplyError(f"the reply is not valid JSON: {error.msg}") from error


def parse_reply(text: str) -> SovereignReply:
    if len(text.encode()) > MAX_REPLY_BYTES:
        raise ReplyError(f"the reply is longer than {MAX_REPLY_BYTES} bytes")
    try:
        return SovereignReply.model_validate(_json_object(text))
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()[:10]
        )
        raise ReplyError(f"the reply does not fit the schema: {problems}") from error


def to_envelope(reply: SovereignReply, report: CouncilReport) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=1,
        civilization_id=report.civilization_id,
        council_day=report.day,
        correlation_id=report.report_id,
        commands=reply.commands,
        rationale=reply.rationale,
    )


def no_commands(report: CouncilReport) -> CommandEnvelope:
    """The envelope of a council that issues nothing new: standing orders carry on."""
    return to_envelope(SovereignReply(), report)


def repair_note(error: ReplyError, reply_text: str) -> str:
    """What a model is told when its first reply could not be read."""
    shown = reply_text[:2_000]
    return (
        "Your previous reply could not be used.\n"
        f"Problem: {error}\n"
        "Your previous reply began:\n"
        f"<previous_reply>\n{shown}\n</previous_reply>\n"
        "Answer again with one JSON object that fits the reply schema exactly."
    )
