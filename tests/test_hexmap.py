import pytest

from sovereign_world.hexmap import HexCoord, Terrain, Tile, WorldMap


def test_hex_coord_has_six_unique_neighbors_in_fixed_order() -> None:
    coord = HexCoord(3, 4)

    assert coord.neighbors() == (
        HexCoord(4, 4),
        HexCoord(4, 3),
        HexCoord(3, 3),
        HexCoord(2, 4),
        HexCoord(2, 5),
        HexCoord(3, 5),
    )


def test_hex_distance_is_symmetric() -> None:
    left = HexCoord(2, 8)
    right = HexCoord(11, 3)

    assert left.distance(right) == 9
    assert right.distance(left) == 9


def test_world_map_indexes_tiles_and_filters_edge_neighbors() -> None:
    tiles = tuple(
        Tile(
            coord=HexCoord(q, r),
            terrain=Terrain.GRASSLAND,
            elevation=500,
            moisture=500,
            temperature=500,
            soil=500,
            timber=500,
            stone=500,
            ore=500,
        )
        for r in range(2)
        for q in range(2)
    )
    world_map = WorldMap(width=2, height=2, tiles=tiles)

    assert world_map.tile(HexCoord(1, 1)).coord == HexCoord(1, 1)
    assert world_map.neighbors(HexCoord(0, 0)) == (HexCoord(1, 0), HexCoord(0, 1))
    assert not world_map.contains(HexCoord(-1, 0))


def test_world_map_rejects_wrong_tile_count() -> None:
    with pytest.raises(ValueError, match="tile count"):
        WorldMap(width=2, height=2, tiles=())
