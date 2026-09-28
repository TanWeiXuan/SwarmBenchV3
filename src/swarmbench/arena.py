"""Deterministic connected 1 m occupancy-grid arenas."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from random import Random
from typing import Any

from .api import Team, Vec2
from .rules import OFFICIAL_RULES, Rules
from .version import GENERATOR_VERSION


class GenerationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Arena:
    width: int
    height: int
    rows: tuple[str, ...]  # indexed by global y, from the lower edge upward

    def blocked(self, x: int, y: int) -> bool:
        return x < 0 or y < 0 or x >= self.width or y >= self.height or self.rows[y][x] == "#"

    def to_dict(self) -> dict[str, Any]:
        return {"width": self.width, "height": self.height, "rows": list(self.rows)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Arena":
        width, height = int(data["width"]), int(data["height"])
        rows = tuple(str(row) for row in data["rows"])
        if width != OFFICIAL_RULES.arena_width or height != OFFICIAL_RULES.arena_height:
            raise ValueError("invalid arena dimensions")
        if len(rows) != height or any(len(row) != width or set(row) - {".", "#"} for row in rows):
            raise ValueError("invalid occupancy grid")
        return cls(width, height, rows)


@dataclass(frozen=True, slots=True)
class Scenario:
    seed: int
    generator_version: int
    arena: Arena
    spawns_a: tuple[Vec2, ...]
    spawns_b: tuple[Vec2, ...]

    def spawns(self, team: Team) -> tuple[Vec2, ...]:
        return self.spawns_a if team is Team.A else self.spawns_b

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "generator_version": self.generator_version,
            "arena": self.arena.to_dict(),
            "spawns_a": [list(point) for point in self.spawns_a],
            "spawns_b": [list(point) for point in self.spawns_b],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scenario":
        if int(data["generator_version"]) != GENERATOR_VERSION:
            raise ValueError("unsupported scenario generator version")
        convert = lambda points: tuple((float(p[0]), float(p[1])) for p in points)
        result = cls(int(data["seed"]), GENERATOR_VERSION, Arena.from_dict(data["arena"]), convert(data["spawns_a"]), convert(data["spawns_b"]))
        if len(result.spawns_a) != 8 or len(result.spawns_b) != 8 or not scenario_is_valid(result):
            raise ValueError("invalid scenario")
        return result


def _base_grid(rules: Rules) -> list[list[str]]:
    grid = [["." for _ in range(rules.arena_width)] for _ in range(rules.arena_height)]
    for x in range(rules.arena_width):
        grid[0][x] = grid[-1][x] = "#"
    for y in range(rules.arena_height):
        grid[y][0] = grid[y][-1] = "#"
    return grid


def _carve_pattern(grid: list[list[str]], rng: Random) -> None:
    height, width = len(grid), len(grid[0])
    # Three porous cross-arena dividers create rooms, junctions and alternate routes.
    for base_x in (31, 60, 89):
        x = base_x + rng.randint(-3, 3)
        gaps = [rng.randint(8, 17), rng.randint(32, 47), rng.randint(62, 71)]
        for y in range(2, height - 2):
            if not any(abs(y - centre) <= 2 for centre in gaps):
                grid[y][x] = "#"
        # Side cover makes each opening a choice rather than a straight shooting lane.
        for index, centre in enumerate(gaps):
            direction = -1 if index % 2 else 1
            for xx in range(x + direction, x + direction * rng.randint(5, 10), direction):
                if 2 <= xx < width - 2 and 2 <= centre + 4 < height - 2:
                    grid[centre + 4][xx] = "#"

    # Short cover blocks and partial room walls; never span an entire zone.
    for zone_left, zone_right in ((12, 27), (36, 54), (66, 84), (94, 108)):
        for _ in range(3):
            y = rng.randint(8, height - 10)
            length = rng.randint(4, 9)
            start = rng.randint(zone_left, max(zone_left, zone_right - length))
            if rng.random() < 0.55:
                for x in range(start, min(zone_right, start + length)):
                    grid[y][x] = "#"
            else:
                x = rng.randint(zone_left, zone_right)
                for yy in range(y, min(height - 2, y + length)):
                    grid[yy][x] = "#"


def _spawn_points(rng: Random, width: int) -> tuple[tuple[Vec2, ...], tuple[Vec2, ...]]:
    offset = rng.uniform(-2.0, 2.0)
    left = []
    for unit_id in range(8):
        column, row = unit_id % 2, unit_id // 2
        left.append((5.5 + column * 2.0, 10.5 + row * 16.0 + offset + rng.uniform(-0.7, 0.7)))
    right = [(width - x, y + rng.uniform(-0.6, 0.6)) for x, y in left]
    return tuple(left), tuple(right)


def _clear_spawn_regions(grid: list[list[str]], points: tuple[Vec2, ...]) -> None:
    for px, py in points:
        cx, cy = int(px), int(py)
        for y in range(max(1, cy - 2), min(len(grid) - 1, cy + 3)):
            for x in range(max(1, cx - 2), min(len(grid[0]) - 1, cx + 3)):
                grid[y][x] = "."


def _free_components(arena: Arena) -> dict[tuple[int, int], int]:
    labels: dict[tuple[int, int], int] = {}
    component = 0
    for y in range(1, arena.height - 1):
        for x in range(1, arena.width - 1):
            if arena.blocked(x, y) or (x, y) in labels:
                continue
            queue = deque([(x, y)])
            labels[(x, y)] = component
            while queue:
                cx, cy = queue.popleft()
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if not arena.blocked(nx, ny) and (nx, ny) not in labels:
                        labels[(nx, ny)] = component
                        queue.append((nx, ny))
            component += 1
    return labels


def scenario_is_valid(scenario: Scenario, rules: Rules = OFFICIAL_RULES) -> bool:
    arena = scenario.arena
    points = scenario.spawns_a + scenario.spawns_b
    if len(points) != rules.units_per_team * 2:
        return False
    for index, point in enumerate(points):
        x, y = point
        if arena.blocked(int(x), int(y)):
            return False
        if any((x - ox) ** 2 + (y - oy) ** 2 < (2 * rules.unit_radius) ** 2 for ox, oy in points[:index]):
            return False
    labels = _free_components(arena)
    spawn_labels = {labels.get((int(x), int(y))) for x, y in points}
    if None in spawn_labels or len(spawn_labels) != 1:
        return False
    # With 1 m cells, a free-cell centre has 0.5 m clearance, exceeding the 0.4 m disc radius.
    return all(sum(not arena.blocked(x, y) for y in range(arena.height)) > arena.height // 3 for x in (2, arena.width // 2, arena.width - 3))


def generate_scenario(seed: int, rules: Rules = OFFICIAL_RULES, retries: int = 24) -> Scenario:
    for attempt in range(retries):
        rng = Random((int(seed) << 8) ^ attempt ^ 0x53A3B3)
        grid = _base_grid(rules)
        _carve_pattern(grid, rng)
        spawns_a, spawns_b = _spawn_points(rng, rules.arena_width)
        _clear_spawn_regions(grid, spawns_a + spawns_b)
        arena = Arena(rules.arena_width, rules.arena_height, tuple("".join(row) for row in grid))
        scenario = Scenario(int(seed), GENERATOR_VERSION, arena, spawns_a, spawns_b)
        if scenario_is_valid(scenario, rules):
            return scenario
    raise GenerationError(f"failed to generate a valid arena after {retries} attempts")
