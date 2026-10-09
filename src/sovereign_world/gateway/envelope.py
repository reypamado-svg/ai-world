"""What a model may write, and how its reply becomes a command envelope.

The model writes only its commands and its reasoning. The gateway fills in which
civilization and which council the envelope belongs to, so a model cannot forge them.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sovereign_world.commands import Command, CommandEnvelope, CouncilReport

REPLY_SCHEMA_VERSION = 1
ENVELOPE_SCHEMA_VERSION = 2
"""Envelopes made from model replies (council-5): orders may count their workers."""
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


DECODING_DROPPED = frozenset(
    {"title", "default", "description", "propertyNames", "maxLength", "minLength"}
)
"""Keywords left out of the decoding schema: notes a grammar has no use for, map keys a
converter ignores, and string lengths a grammar would spell out character by character.
Validation still enforces every one of them."""


def _for_decoding(node: object) -> object:
    if isinstance(node, list):
        return [_for_decoding(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, object] = {}
    for key, value in node.items():
        if key in DECODING_DROPPED:
            continue
        if key in {"properties", "$defs"} and isinstance(value, dict):
            out[key] = {name: _for_decoding(schema) for name, schema in value.items()}
        else:
            out[key] = _for_decoding(value)
    if isinstance(out.get("properties"), dict):
        out["additionalProperties"] = False
    return out


def decoding_schema() -> dict[str, object]:
    """The reply schema as a local model's decoder is held to it: every command has its kind,
    every value is one the engine knows, and no key is invented.

    It is derived from `reply_schema()` (which the charter shows and the seal pins) and only
    narrows what may be written; `parse_reply` still validates every reply in full. The
    reasoning comes before the commands, so a decoder that keeps the schema's order writes
    why before what."""
    schema = _for_decoding(reply_schema())
    assert isinstance(schema, dict)
    properties = schema["properties"]
    assert isinstance(properties, dict)
    schema["properties"] = {
        "rationale": properties["rationale"],
        "commands": properties["commands"],
    }
    return schema


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
        schema_version=ENVELOPE_SCHEMA_VERSION,
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
