"""Languages: every civilization speaks its own, and people learn others by living among
their speakers. An envoy's fluency decides how faithfully a message arrives."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from sovereign_world.hexmap import HexCoord
from sovereign_world.ids import EntityId
from sovereign_world.people import Person
from sovereign_world.people_store import PeopleTable

FLUENT = 50
"""Fluency at which a person counts as a speaker others can learn from."""
LEARNING_INTERVAL = 5
"""Every this many days among speakers of a language, a person gains a point in it."""
GESTURE_FIDELITY = 20
"""Even with no shared words, gestures and names carry this much of a message."""
WRITTEN_BONUS = 30
"""An envoy who writes carries the message down, and loses less of it."""
WRITING = "writing"
LOST_WORD = "…"


def native(person: Person) -> EntityId:
    """The language a person grew up speaking: that of the civilization they were born to."""
    return person.native_language or person.civilization_id


def fluency(person: Person, language: EntityId) -> int:
    if native(person) == language:
        return 100
    return person.languages.get(language, 0)


def speaks(person: Person, language: EntityId) -> bool:
    return fluency(person, language) >= FLUENT


def fidelity(envoy: Person, language: EntityId) -> int:
    """How much of a message, in percent, survives an envoy's telling in a language."""
    carried = fluency(envoy, language) + (WRITTEN_BONUS if envoy.skills.get(WRITING, 0) > 0 else 0)
    return max(GESTURE_FIDELITY, min(100, carried))


def render(text: str, percent: int, roll: np.random.Generator) -> str:
    """The message as it arrives: each word survives with the envoy's fidelity, or is lost."""
    if percent >= 100:
        return text
    return " ".join(
        word if int(roll.integers(0, 100)) < percent else LOST_WORD for word in text.split(" ")
    )


def learn(people: Iterable[Person]) -> int:
    """Everyone gains a point in each language spoken by someone standing with them.

    Returns how many points were gained. Called once every `LEARNING_INTERVAL` days.
    """
    living = [person for person in people if person.alive]
    heard: dict[HexCoord, set[EntityId]] = {}
    for person in living:
        tongues = heard.setdefault(person.location, set())
        tongues.add(native(person))
        tongues.update(language for language, level in person.languages.items() if level >= FLUENT)
    gained = 0
    for person in living:
        own = native(person)
        for language in sorted(heard[person.location]):
            if language == own:
                continue
            level = person.languages.get(language, 0)
            if level < 100:
                person.languages = {**person.languages, language: level + 1}
                gained += 1
    return gained


def learn_tables(tables: Iterable[PeopleTable]) -> int:
    """`learn` over whole tables (Phase 5 S6): the same points, gained in the same order,
    but read from the columns, so people who learn nothing are not touched."""
    living = [
        (table, row) for table in tables for row in np.flatnonzero(table.mask(alive=True)).tolist()
    ]
    heard: dict[int, set[EntityId]] = {}
    natives: list[EntityId] = []
    for table, row in living:
        own = table.objs["native_language"][row] or table.objs["civilization_id"][row]
        natives.append(own)
        tongues = heard.setdefault(int(table.loc_code[row]), set())
        tongues.add(own)
        tongues.update(
            language for language, level in table.objs["languages"][row].items() if level >= FLUENT
        )
    spoken = {code: sorted(tongues) for code, tongues in heard.items()}
    gained = 0
    for (table, row), own in zip(living, natives, strict=True):
        languages = spoken[int(table.loc_code[row])]
        if len(languages) == 1 and languages[0] == own:
            continue
        for language in languages:
            if language == own:
                continue
            known = table.objs["languages"][row]
            level = known.get(language, 0)
            if level < 100:
                table.person(row).languages = {**known, language: level + 1}
                gained += 1
    return gained
