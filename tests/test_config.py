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
    [
        ("width", 23),
        ("height", 0),
        ("civilizations", 1),
        ("civilizations", 5),
        ("founders_per_civilization", 31),
    ],
)
def test_world_config_rejects_values_outside_world_contract(field: str, value: int) -> None:
    values = {"seed": 41, "width": 48, "height": 48, field: value}

    with pytest.raises(ValidationError):
        WorldConfig(**values)


@pytest.mark.parametrize("count", [2, 3, 4])
def test_world_config_accepts_two_to_four_civilizations(count: int) -> None:
    assert WorldConfig(seed=41, width=48, height=48, civilizations=count).civilizations == count


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


def _pinned_manifest(**extra: object) -> RunManifest:
    from sovereign_world.config import BudgetConfig, SovereignConfig

    return RunManifest(
        run_id=UUID("00000000-0000-4000-8000-000000000021"),
        engine_version="0.2.0",
        config=WorldConfig(seed=21, width=48, height=48),
        sovereigns={
            "civilization:0000000001": SovereignConfig(
                provider="anthropic", model="claude-opus-5-5"
            ),
            "civilization:0000000002": SovereignConfig(
                provider="compatible", model="llama3", base_url="http://127.0.0.1:11434/v1"
            ),
        },
        budgets=BudgetConfig(timeout_seconds=120.0),
        generator_version=3,
        rules_version=3,
        journal_format=2,
        **extra,  # type: ignore[arg-type]
    )


def test_a_manifest_with_sovereigns_and_budgets_keeps_its_pinned_hash() -> None:
    """Pinned before the sealed trial added settings: an existing run's manifest keeps its
    hash however many settings later versions add, as long as they are left at their defaults."""
    assert (
        _pinned_manifest().content_hash()
        == "89ad1fc59b2b6e468dd4801e7c6b1365654cb5de62bb0581367c88db91cfa4ac"
    )
