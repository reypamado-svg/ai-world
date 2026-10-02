"""Rules versions: which mechanics a world runs under.

Old worlds keep the rules they were made with, so their runs replay exactly; new worlds use
the current rules, and a fork keeps its parent's. Each version is a set of switches the
engine and the command validator read.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Rules:
    version: int
    houses: bool
    """Every five people need a house; conception happens only where there is room."""
    decrees_expire: bool
    """A decree ends when its duration runs out unless it is issued again."""
    ranks: bool
    """Settlements and the realm earn ranks, and ranks unlock works and orders."""
    civil_research: bool
    """Writing, irrigation, herbal care, fishing, surveying and organised logistics."""


def rules_for(version: int) -> Rules:
    second = version >= 2
    return Rules(
        version=version,
        houses=second,
        decrees_expire=second,
        ranks=second,
        civil_research=second,
    )
