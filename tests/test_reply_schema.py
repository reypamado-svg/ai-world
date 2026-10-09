"""The schema a local model's decoder is held to (slice K): derived from the reply schema the
charter shows and the seal pins, it only narrows what may be written, and leaves the reply
schema itself, its hash and every reply that was valid unchanged."""

from __future__ import annotations

import json

from sovereign_world.gateway.envelope import (
    DECODING_DROPPED,
    SovereignReply,
    decoding_schema,
    parse_reply,
    reply_schema,
)
from sovereign_world.seal import reply_schema_hash

REPLY_SCHEMA_HASH = "2de96b187ab698c507c42137705e0fb2fe974b4ebec34ca6254f19a7d43172ae"
"""The reply schema's hash before slice K: the charter and the seal see the same schema."""


def _schemas(node: object) -> list[dict[str, object]]:
    """Every schema in the tree, skipping property names (which are not keywords)."""
    if isinstance(node, list):
        return [found for item in node for found in _schemas(item)]
    if not isinstance(node, dict):
        return []
    found = [node]
    for key, value in node.items():
        if key in {"properties", "$defs"} and isinstance(value, dict):
            found += [schema for item in value.values() for schema in _schemas(item)]
        elif isinstance(value, (dict, list)):
            found += _schemas(value)
    return found


def test_the_reply_schema_and_its_hash_are_unchanged() -> None:
    assert reply_schema() == SovereignReply.model_json_schema()
    assert reply_schema_hash() == REPLY_SCHEMA_HASH


def test_the_decoding_schema_keeps_every_definition_and_drops_only_notes() -> None:
    schema = decoding_schema()
    full = reply_schema()
    assert set(schema["$defs"]) == set(full["$defs"])  # type: ignore[call-overload]
    for found in _schemas(schema):
        assert not DECODING_DROPPED & set(found), found
    assert decoding_schema() == schema  # a fresh copy each time, always the same
    assert reply_schema() == full  # deriving it changed nothing


def test_every_command_has_its_kind_and_no_key_is_invented() -> None:
    schema = decoding_schema()
    defs = schema["$defs"]
    assert isinstance(defs, dict)
    assert defs["Decree"]["required"] == ["command_id", "kind", "value"]
    assert defs["DirectOrder"]["required"] == ["command_id", "kind"]
    assert defs["DefenceWork"]["enum"] == ["gatehouse", "ditch", "moat", "stakes", "citadel"]
    for found in _schemas(schema):
        if "properties" in found:
            assert found["additionalProperties"] is False, found
    # The resource maps keep their numbers.
    cargo = defs["DirectOrder"]["properties"]["cargo"]
    assert cargo == {"additionalProperties": {"type": "integer"}, "type": "object"}


def test_the_reasoning_comes_before_the_commands() -> None:
    schema = decoding_schema()
    assert list(schema["properties"]) == ["rationale", "commands"]  # type: ignore[call-overload]
    assert schema["properties"]["commands"]["maxItems"] == 8  # type: ignore[index]


def test_a_reply_the_decoder_may_write_is_still_read() -> None:
    reply = parse_reply(
        json.dumps(
            {
                "rationale": "Guard the gates.",
                "commands": [
                    {"command_id": "r", "kind": "food_reserve_target", "value": 70},
                    {
                        "command_id": "g",
                        "kind": "build_works",
                        "settlement_id": "settlement:0000000001",
                        "work": "gatehouse",
                        "section_ids": [0],
                    },
                ],
            }
        )
    )
    assert [command.kind for command in reply.commands] == ["food_reserve_target", "build_works"]
