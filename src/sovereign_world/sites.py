"""Places worth going to: ore deposits, quarries, ancient ruins and troves.

World generator version 3 places the same small kit near every
civilization, so no one starts richer than another: two ore deposits, two
quarries, an ancient ruin and a trove, each at a similar distance from its
own start (as the land allows) and nearer to it than to any other start.
Sites are only data for now; the rules that work them come later.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from sovereign_world.hexmap import HexCoord, Terrain, WorldMap
from sovereign_world.ids import EntityId


class SiteKind(StrEnum):
    ORE_DEPOSIT = "ore_deposit"
    QUARRY = "quarry"
    ANCIENT_RUIN = "ancient_ruin"
    """Remains of a people from before the founders, unrelated to fallen civilizations."""
    TROVE = "trove"
    HUNTING_GROUND = "hunting_ground"
    """Reserved for wild animals; never placed yet."""


class Site(BaseModel):
    model_config = ConfigDict(frozen=True)

    site_id: EntityId
    tile: HexCoord
    kind: SiteKind
    richness: int = Field(ge=0)
    """What the site held when the world was made (units, or 1 for a single find)."""
    remaining: int = Field(ge=0)
    opened_day: int | None = None
    spent_day: int | None = None


KIT: tuple[tuple[SiteKind, int, int], ...] = (
    (SiteKind.ORE_DEPOSIT, 3, 5),
    (SiteKind.ORE_DEPOSIT, 6, 9),
    (SiteKind.QUARRY, 2, 4),
    (SiteKind.QUARRY, 5, 8),
    (SiteKind.ANCIENT_RUIN, 5, 9),
    (SiteKind.TROVE, 8, 12),
)
"""Each civilization's sites: kind and the band of distances from its own start, in tiles."""

RICHNESS: dict[SiteKind, int] = {
    SiteKind.ORE_DEPOSIT: 2_400,
    SiteKind.QUARRY: 6_000,
    SiteKind.ANCIENT_RUIN: 1,
    SiteKind.TROVE: 1,
}

RANKED_BY: dict[SiteKind, str] = {
    SiteKind.ORE_DEPOSIT: "ore",
    SiteKind.QUARRY: "stone",
    SiteKind.ANCIENT_RUIN: "soil",
    SiteKind.TROVE: "timber",
}
"""Within its band, a site goes where this tile value is highest: ore and stone where they lie,
a ruin on once-farmed soil, a trove hidden in the woods."""

START_CLEARANCE = 2
"""No site lies this close to any start, or closer."""
SITE_SPACING = 3
"""Sites lie at least this far apart."""


def place_sites(
    world_map: WorldMap, centres: tuple[HexCoord, ...], landmass: frozenset[HexCoord]
) -> tuple[Site, ...] | None:
    """Each start's kit of sites, or None when the land cannot hold a fair set."""
    taken: list[HexCoord] = []
    sites: list[Site] = []
    for centre in centres:
        others = [other for other in centres if other != centre]
        for kind, low, high in KIT:
            chosen: HexCoord | None = None
            for widen in (0, 1):
                least, most = max(1, low - widen), high + widen
                best: tuple[int, int, int] | None = None
                for dr in range(-most, most + 1):
                    for dq in range(-most, most + 1):
                        coord = HexCoord(centre.q + dq, centre.r + dr)
                        own = coord.distance(centre)
                        if not least <= own <= most or coord not in landmass:
                            continue
                        if world_map.tile(coord).terrain is Terrain.WATER:
                            continue
                        if any(coord.distance(start) <= START_CLEARANCE for start in centres):
                            continue
                        # Strictly nearer its own start than any other.
                        if any(coord.distance(other) <= own for other in others):
                            continue
                        if any(coord.distance(site) < SITE_SPACING for site in taken):
                            continue
                        key = (-getattr(world_map.tile(coord), RANKED_BY[kind]), coord.q, coord.r)
                        if best is None or key < best:
                            best = key
                if best is not None:
                    chosen = HexCoord(best[1], best[2])
                    break
            if chosen is None:
                return None
            taken.append(chosen)
            sites.append(
                Site(
                    site_id=EntityId(f"site:{len(sites) + 1:010d}"),
                    tile=chosen,
                    kind=kind,
                    richness=RICHNESS[kind],
                    remaining=RICHNESS[kind],
                )
            )
    return tuple(sorted(sites, key=lambda site: site.tile))
