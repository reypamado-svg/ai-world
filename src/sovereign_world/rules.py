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
    cover_mechanics: bool
    """Food, timber and stone follow the land cover; new settlements need water."""
    sites: bool
    """Deposits, quarries, ancient ruins and troves can be worked."""
    reach_cap: bool
    """A settlement's influence stops growing at a town's, so borders stay local however
    large a city grows."""
    worker_counts: bool
    """Orders for work at home may count their workers at a settlement instead of naming
    them."""
    town_plans: bool
    """Councils design their settlements (rules version 3): walls go up section by section
    along the planned ring, and the keep, market and craft quarter count where they stand."""


def rules_for(version: int) -> Rules:
    second = version >= 2
    third = version >= 3
    return Rules(
        version=version,
        houses=second,
        decrees_expire=second,
        ranks=second,
        civil_research=second,
        cover_mechanics=second,
        sites=second,
        reach_cap=second,
        worker_counts=second,
        town_plans=third,
    )
