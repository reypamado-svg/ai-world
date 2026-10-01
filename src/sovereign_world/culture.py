"""Culture and assimilation: newcomers keep their ancestry, memories and language while,
month by month, they become people of the civilization they now live in.

Assimilation is a record; nobody is held back for being unassimilated.
"""

from __future__ import annotations

from sovereign_world.ids import EntityId
from sovereign_world.languages import native, speaks
from sovereign_world.people import Person

ASSIMILATION_INTERVAL = 30
"""Assimilation advances once every this many days."""
MONTHLY_ASSIMILATION = 3
"""Points a newcomer gains toward their civilization's culture each month."""
FLUENT_ASSIMILATION = 3
"""Extra points each month for a newcomer who speaks their civilization's language."""
ASSIMILATED = 100


def culture(person: Person) -> EntityId:
    """The culture a person lives by: that of their civilization, until they move."""
    return person.culture or person.civilization_id


def ancestry(person: Person) -> tuple[EntityId, ...]:
    """The cultures a person's forebears came from, kept for life."""
    return person.ancestry or (native(person),)


def assimilate(person: Person) -> bool:
    """A month's assimilation; returns whether the person has now fully assimilated."""
    if person.culture is None or not person.alive or person.captive_of is not None:
        return False
    gain = MONTHLY_ASSIMILATION + (
        FLUENT_ASSIMILATION if speaks(person, person.civilization_id) else 0
    )
    progress = person.assimilation + gain
    if progress < ASSIMILATED:
        person.assimilation = progress
        return False
    person.culture = None
    person.assimilation = 0
    return True
