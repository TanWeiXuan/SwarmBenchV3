"""Shared deterministic geometry for physics, sensors, shots and unit-view rendering."""

from __future__ import annotations

import math
from collections.abc import Iterable

from .api import Vec2
from .arena import Arena

EPS = 1e-9


def clamp_norm(vector: Vec2, maximum: float) -> Vec2:
    length = math.hypot(*vector)
    if length <= maximum or length <= EPS:
        return vector
    scale = maximum / length
    return vector[0] * scale, vector[1] * scale


def approach_vector(current: Vec2, target: Vec2, maximum_change: float) -> Vec2:
    delta = target[0] - current[0], target[1] - current[1]
    change = clamp_norm(delta, maximum_change)
    return current[0] + change[0], current[1] + change[1]


def normalize_angle(angle: float) -> float:
    value = (angle + math.pi) % (2 * math.pi) - math.pi
    return 0.0 if abs(value) < EPS else value


def shortest_angle_delta(current: float, target: float) -> float:
    """Shortest signed turn; an exact 180-degree tie always turns counterclockwise."""
    delta = normalize_angle(target - current)
    return math.pi if abs(delta + math.pi) <= EPS else delta


def trace_cells(start: Vec2, end: Vec2) -> tuple[tuple[int, int], ...]:
    """Amanatides-Woo supercover; corner crossings include both adjacent cells."""
    x0, y0 = start
    x1, y1 = end
    cx, cy = math.floor(x0), math.floor(y0)
    ex, ey = math.floor(x1), math.floor(y1)
    cells: list[tuple[int, int]] = [(cx, cy)]
    dx, dy = x1 - x0, y1 - y0
    step_x = 1 if dx > 0 else -1 if dx < 0 else 0
    step_y = 1 if dy > 0 else -1 if dy < 0 else 0
    t_delta_x = abs(1.0 / dx) if dx else math.inf
    t_delta_y = abs(1.0 / dy) if dy else math.inf
    next_x = (cx + 1 if step_x > 0 else cx) if step_x else 0
    next_y = (cy + 1 if step_y > 0 else cy) if step_y else 0
    t_max_x = (next_x - x0) / dx if dx else math.inf
    t_max_y = (next_y - y0) / dy if dy else math.inf
    guard = 0
    while (cx, cy) != (ex, ey) and guard < 10000:
        guard += 1
        if abs(t_max_x - t_max_y) <= EPS:
            if step_x and (cx + step_x, cy) not in cells:
                cells.append((cx + step_x, cy))
            if step_y and (cx, cy + step_y) not in cells:
                cells.append((cx, cy + step_y))
            cx += step_x
            cy += step_y
            t_max_x += t_delta_x
            t_max_y += t_delta_y
        elif t_max_x < t_max_y:
            cx += step_x
            t_max_x += t_delta_x
        else:
            cy += step_y
            t_max_y += t_delta_y
        if (cx, cy) not in cells:
            cells.append((cx, cy))
    return tuple(cells)


def wall_clear(arena: Arena, start: Vec2, end: Vec2, *, endpoint_wall_visible: bool = False) -> bool:
    cells = trace_cells(start, end)
    target = (math.floor(end[0]), math.floor(end[1]))
    for cell in cells[1:]:
        if arena.blocked(*cell):
            return endpoint_wall_visible and cell == target
    return True


def circle_hits_wall(arena: Arena, position: Vec2, radius: float) -> bool:
    px, py = position
    for y in range(math.floor(py - radius), math.floor(py + radius) + 1):
        for x in range(math.floor(px - radius), math.floor(px + radius) + 1):
            if not arena.blocked(x, y):
                continue
            qx = min(max(px, x), x + 1.0)
            qy = min(max(py, y), y + 1.0)
            if (px - qx) ** 2 + (py - qy) ** 2 < radius * radius - EPS:
                return True
    return False


def slide_move(arena: Arena, start: Vec2, end: Vec2, radius: float) -> Vec2:
    """Conservative substep movement with deterministic x/y sliding."""
    if not circle_hits_wall(arena, end, radius):
        return end
    x_only = (end[0], start[1])
    y_only = (start[0], end[1])
    x_ok, y_ok = not circle_hits_wall(arena, x_only, radius), not circle_hits_wall(arena, y_only, radius)
    if x_ok and y_ok:
        return x_only if abs(end[0] - start[0]) >= abs(end[1] - start[1]) else y_only
    if x_ok:
        return x_only
    if y_ok:
        return y_only
    return start


def swept_pair_distance(a0: Vec2, a1: Vec2, b0: Vec2, b1: Vec2) -> float:
    rx, ry = a0[0] - b0[0], a0[1] - b0[1]
    vx, vy = (a1[0] - a0[0]) - (b1[0] - b0[0]), (a1[1] - a0[1]) - (b1[1] - b0[1])
    vv = vx * vx + vy * vy
    t = 0.0 if vv <= EPS else min(1.0, max(0.0, -(rx * vx + ry * vy) / vv))
    return math.hypot(rx + vx * t, ry + vy * t)


def ray_circle(origin: Vec2, direction: Vec2, center: Vec2, radius: float) -> float | None:
    ox, oy = origin[0] - center[0], origin[1] - center[1]
    projection = -(ox * direction[0] + oy * direction[1])
    discriminant = projection * projection - (ox * ox + oy * oy - radius * radius)
    if discriminant < -EPS:
        return None
    root = math.sqrt(max(0.0, discriminant))
    values = [value for value in (projection - root, projection + root) if value >= -EPS]
    return max(0.0, min(values)) if values else None


def ray_aabb(origin: Vec2, direction: Vec2, x: int, y: int) -> float | None:
    near, far = -math.inf, math.inf
    for coordinate, component, low, high in ((origin[0], direction[0], x, x + 1), (origin[1], direction[1], y, y + 1)):
        if abs(component) <= EPS:
            if coordinate < low - EPS or coordinate > high + EPS:
                return None
            continue
        left, right = (low - coordinate) / component, (high - coordinate) / component
        near, far = max(near, min(left, right)), min(far, max(left, right))
    return max(0.0, near) if far >= max(near, 0.0) - EPS else None


def first_wall_hit(arena: Arena, origin: Vec2, direction: Vec2, maximum: float) -> tuple[float, Vec2] | None:
    end = origin[0] + direction[0] * maximum, origin[1] + direction[1] * maximum
    best: float | None = None
    for x, y in trace_cells(origin, end)[1:]:
        if arena.blocked(x, y):
            distance = ray_aabb(origin, direction, x, y)
            if distance is not None and distance <= maximum + EPS and (best is None or distance < best):
                best = distance
    return None if best is None else (best, (origin[0] + direction[0] * best, origin[1] + direction[1] * best))


def visible_terrain(arena: Arena, position: Vec2, heading: float, visual_range: float, fov: float, close_range: float) -> tuple[tuple[int, int, bool], ...]:
    cells: set[tuple[int, int]] = {(math.floor(position[0]), math.floor(position[1]))}
    rays: list[tuple[float, float]] = []
    for index in range(81):
        rays.append((heading - fov / 2 + fov * index / 80, visual_range))
    for index in range(48):
        rays.append((2 * math.pi * index / 48, close_range))
    for angle, distance in rays:
        end = position[0] + math.cos(angle) * distance, position[1] + math.sin(angle) * distance
        for cell in trace_cells(position, end):
            cells.add(cell)
            if arena.blocked(*cell):
                break
    return tuple((x, y, arena.blocked(x, y)) for x, y in sorted(cells) if 0 <= x < arena.width and 0 <= y < arena.height)


def unit_visible(arena: Arena, observer: Vec2, heading: float, target: Vec2, visual_range: float, fov: float, close_range: float) -> bool:
    dx, dy = target[0] - observer[0], target[1] - observer[1]
    distance = math.hypot(dx, dy)
    in_close = distance <= close_range + EPS
    angle = abs(normalize_angle(math.atan2(dy, dx) - heading))
    in_cone = distance <= visual_range + EPS and angle <= fov / 2 + EPS
    return (in_close or in_cone) and wall_clear(arena, observer, target)


def segment_blocked_by_friendly(origin: Vec2, target: Vec2, friendlies: Iterable[tuple[Vec2, float]]) -> bool:
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length = math.hypot(dx, dy)
    if length <= EPS:
        return False
    direction = dx / length, dy / length
    return any((hit := ray_circle(origin, direction, position, radius)) is not None and hit < length for position, radius in friendlies)
