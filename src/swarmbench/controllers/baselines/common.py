"""Small local-perception navigation shared by the deliberately simple baselines."""

from __future__ import annotations

import math
from typing import Iterable

from swarmbench import Action, Observation, Team, UnitInfo, VisibleUnit


def angle_delta(current: float, target: float) -> float:
    value = (target - current + math.pi) % (2 * math.pi) - math.pi
    return math.pi if abs(value + math.pi) < 1e-12 else value


def direction(start: tuple[float, float], target: tuple[float, float], speed: float) -> tuple[float, float]:
    dx, dy = target[0] - start[0], target[1] - start[1]
    length = math.hypot(dx, dy)
    return (0.0, 0.0) if length < 1e-9 else (dx * speed / length, dy * speed / length)


def friendly_in_line(origin: tuple[float, float], target: tuple[float, float], friends: Iterable[VisibleUnit], radius: float = 0.48) -> bool:
    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length2 = dx * dx + dy * dy
    if length2 <= 1e-12:
        return False
    for friend in friends:
        fx, fy = friend.position[0] - origin[0], friend.position[1] - origin[1]
        t = max(0.0, min(1.0, (fx * dx + fy * dy) / length2))
        if t < 0.98 and math.hypot(fx - dx * t, fy - dy * t) < radius:
            return True
    return False


class SimpleNavigator:
    def initialize_common(self, info: UnitInfo, *, spread: bool) -> None:
        self.info = info
        self.known: dict[tuple[int, int], bool] = {}
        self.blocked_steps = 0
        self.last_position = info.starting_state.position
        lane = 6.0 + (info.unit_id + 0.5) * (68.0 / info.initial_team_size) if spread else 40.0
        self.base_goal = (116.0 if info.team is Team.A else 4.0, lane)

    def _remember(self, observation: Observation) -> None:
        for cell in observation.visible_terrain:
            self.known[(cell.x, cell.y)] = cell.blocked

    def _path_blocked(self, origin: tuple[float, float], angle: float, distance: float = 3.0) -> bool:
        for step in range(1, 9):
            scale = distance * step / 8
            cell = (int(origin[0] + math.cos(angle) * scale), int(origin[1] + math.sin(angle) * scale))
            if self.known.get(cell, False):
                return True
        return False

    def movement(self, observation: Observation, target: tuple[float, float]) -> tuple[float, float]:
        self._remember(observation)
        position = observation.self_state.position
        moved = math.dist(position, self.last_position)
        self.blocked_steps = self.blocked_steps + 1 if moved < 0.025 else 0
        self.last_position = position
        preferred = math.atan2(target[1] - position[1], target[0] - position[0])
        sign = -1 if self.info.unit_id % 2 else 1
        candidates = [preferred, preferred + sign * math.pi / 4, preferred - sign * math.pi / 4, preferred + sign * math.pi / 2, preferred - sign * math.pi / 2]
        if self.blocked_steps > 8:
            candidates = candidates[2:] + candidates[:2]
        selected = next((angle for angle in candidates if not self._path_blocked(position, angle)), candidates[-1])
        vx, vy = math.cos(selected) * self.info.rules.max_speed, math.sin(selected) * self.info.rules.max_speed
        # Visible local repulsion reduces immediate bunching without any hidden/shared state.
        for friend in observation.visible_friendlies:
            distance = math.dist(position, friend.position)
            if 1e-6 < distance < 1.5:
                scale = (1.5 - distance) * 2.0 / distance
                vx += (position[0] - friend.position[0]) * scale
                vy += (position[1] - friend.position[1]) * scale
        length = math.hypot(vx, vy)
        if length > self.info.rules.max_speed:
            vx, vy = vx * self.info.rules.max_speed / length, vy * self.info.rules.max_speed / length
        return vx, vy

    def combat_action(self, observation: Observation, destination: tuple[float, float], *, broadcast: int | None = None) -> Action:
        position = observation.self_state.position
        enemies = sorted(observation.visible_enemies, key=lambda enemy: (math.dist(position, enemy.position), enemy.unit_id))
        target = enemies[0].position if enemies else destination
        heading = math.atan2(target[1] - position[1], target[0] - position[0])
        velocity = self.movement(observation, destination)
        aligned = abs(angle_delta(observation.self_state.heading, heading)) <= math.radians(4.0)
        clear = not enemies or not friendly_in_line(position, enemies[0].position, observation.visible_friendlies)
        fire = bool(enemies and aligned and clear and math.dist(position, enemies[0].position) <= self.info.rules.weapon_range)
        reload_request = observation.self_state.ammunition == 0 or (observation.self_state.ammunition <= 2 and not enemies)
        return Action(velocity, heading, fire, reload_request, broadcast)
