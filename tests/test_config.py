from uuid import UUID

import pytest
from pydantic import ValidationError

from sovereign_world.config import RunManifest, WorldConfig


def test_world_config_has_locked_civilization_shape() -> None:
    config = WorldConfig(seed=41, width=48, height=48)

    assert config.civilizations == 4
    assert config.founders_per_civilization == 32
    assert config.council_interval_days == 30


@pytest.mark.parametrize(
    ("field", "value"),
    [("width", 23), ("height", 0), ("civilizations", 3), ("founders_per_civilization", 31)],
)
def test_world_config_rejects_values_outside_world_contract(field: str, value: int) -> None:
    values = {"seed": 41, "width": 48, "height": 48, field: value}

    with pytest.raises(ValidationError):
        WorldConfig(**values)


def test_world_config_is_immutable() -> None:
    config = WorldConfig(seed=41, width=48, height=48)

    with pytest.raises(ValidationError):
        config.width = 64


def test_manifest_hash_survives_round_trip() -> None:
    config = WorldConfig(seed=41, width=48, height=48)
    manifest = RunManifest.new(config=config, engine_version="0.1.0")

    restored = RunManifest.model_validate_json(manifest.model_dump_json())

    assert isinstance(restored.run_id, UUID)
    assert restored.content_hash() == manifest.content_hash()


def test_manifest_hash_covers_engine_version() -> None:
    config = WorldConfig(seed=41, width=48, height=48)
    original = RunManifest.new(config=config, engine_version="0.1.0")
    changed = original.model_copy(update={"engine_version": "0.2.0"})

    assert changed.content_hash() != original.content_hash()

