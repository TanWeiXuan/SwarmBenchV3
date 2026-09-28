import math

from swarmbench.api import Team
from swarmbench.arena import generate_scenario, scenario_is_valid
from swarmbench.geometry import trace_cells, unit_visible, visible_terrain, wall_clear

from .helpers import scenario_with_walls


def test_many_seed_arenas_are_deterministic_connected_and_spawn_safe() -> None:
    for seed in range(30):
        left, right = generate_scenario(seed), generate_scenario(seed)
        assert left.to_dict() == right.to_dict()
        assert scenario_is_valid(left)
        assert len(left.spawns_a) == len(left.spawns_b) == 8
        assert min(math.dist(a, b) for a in left.spawns_a for b in left.spawns_b) > 22


def test_fov_range_and_close_awareness_boundaries() -> None:
    arena = scenario_with_walls().arena
    origin = (20.5, 20.5)
    assert unit_visible(arena, origin, 0.0, (38.5, 20.5), 18, 2 * math.pi / 3, 3)
    assert not unit_visible(arena, origin, 0.0, (38.5001, 20.5), 18, 2 * math.pi / 3, 3)
    edge = (20.5 + 10 * math.cos(math.pi / 3), 20.5 + 10 * math.sin(math.pi / 3))
    assert unit_visible(arena, origin, 0.0, edge, 18, 2 * math.pi / 3, 3)
    assert unit_visible(arena, origin, 0.0, (18.5, 20.5), 18, 2 * math.pi / 3, 3)


def test_walls_occlude_close_awareness_and_first_wall_is_visible() -> None:
    arena = scenario_with_walls({(21, 20)}).arena
    origin = (20.5, 20.5)
    assert not unit_visible(arena, origin, 0.0, (22.5, 20.5), 18, 2 * math.pi / 3, 3)
    cells = {(x, y): blocked for x, y, blocked in visible_terrain(arena, origin, 0, 18, 2 * math.pi / 3, 3)}
    assert cells[(21, 20)] is True
    assert (22, 20) not in cells


def test_supercover_blocks_zero_width_diagonal_cracks() -> None:
    arena = scenario_with_walls({(11, 10), (10, 11)}).arena
    cells = trace_cells((10.5, 10.5), (11.5, 11.5))
    assert (11, 10) in cells and (10, 11) in cells
    assert not wall_clear(arena, (10.5, 10.5), (11.5, 11.5))
