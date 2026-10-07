"""Checks a run must pass before it is sealed (sealed trial).

`offline_checks` asks no model and changes nothing; `seal` refuses a run that fails any. The
launch gate (`sovereign-world preflight`, slice E) builds on it.
"""

from __future__ import annotations

from sovereign_world.config import RunManifest
from sovereign_world.persistence import HEADER, WorldStore
from sovereign_world.state import WorldState


def offline_checks(store: WorldStore, manifest: RunManifest, state: WorldState) -> tuple[str, ...]:
    """What stops this run from being sealed, in plain words; empty when nothing does."""
    from sovereign_world.gateway.prompt import PROMPT_VERSION
    from sovereign_world.seal import pins_of

    problems: list[str] = []
    if state.day != 0:
        problems.append(f"the run stands at day {state.day}; only a run on day 0 can be sealed")
    if manifest.journal_format < 2:
        problems.append("the journal is format 1; only a format-2 run can be sealed")
    elif [record.type for record in store.read_records()] != [HEADER]:
        problems.append("the journal holds more than its header; a sealed run starts unsaved")
    if store.seal_document() is not None:
        problems.append("the run is already sealed")
    known = {str(civilization_id) for civilization_id in state.civilizations}
    for civilization_id, config in sorted(manifest.sovereigns.items()):
        if civilization_id not in known:
            problems.append(f"{civilization_id} is named in the settings but not in the world")
        if config.provider == "baseline":
            continue
        if config.prompt_version != PROMPT_VERSION:
            problems.append(
                f"{civilization_id} is set to prompt {config.prompt_version}, not this engine's"
                f" {PROMPT_VERSION}"
            )
        if config.provider in ("openai", "compatible") and not config.model:
            problems.append(f"{civilization_id} names no model")
    try:
        pins_of(manifest)
    except ValueError as error:
        problems.append(str(error))
    return tuple(problems)
