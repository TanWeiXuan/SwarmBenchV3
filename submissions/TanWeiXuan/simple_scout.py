"""A small lane-based community controller for SwarmBenchV3."""

from __future__ import annotations

import math

from swarmbench import Action, BaseUnitController, Observation, Team, UnitInfo, VisibleUnit


def angle_error(current: float, target: float) -> float:
    """Return the shortest signed turn from ``current`` to ``target``."""

    return (target - current + math.pi) % (2.0 * math.pi) - math.pi


def friend_blocks_shot(
    origin: tuple[float, float],
    target: tuple[float, float],
    friendlies: tuple[VisibleUnit, ...],
) -> bool:
    """Conservatively reject shots with a visible friendly near the aim line."""

    dx, dy = target[0] - origin[0], target[1] - origin[1]
    length_squared = dx * dx + dy * dy
    if length_squared < 1e-9:
        return False
    for friendly in friendlies:
        fx = friendly.position[0] - origin[0]
        fy = friendly.position[1] - origin[1]
        along = max(0.0, min(1.0, (fx * dx + fy * dy) / length_squared))
        if along < 0.98 and math.hypot(fx - along * dx, fy - along * dy) < 0.55:
            return True
    return False


class UnitController(BaseUnitController):
    """Advance in a separate lane and fight the closest visible opponent."""

    def initialize(self, info: UnitInfo) -> None:
        self.info = info
        self.known_walls: set[tuple[int, int]] = set()
        self.last_position = info.starting_state.position
        self.stuck_ticks = 0

        lane_spacing = 68.0 / info.initial_team_size
        lane_y = 6.0 + (info.unit_id + 0.5) * lane_spacing
        goal_x = info.arena_size[0] - 4.0 if info.team is Team.A else 4.0
        self.goal = (goal_x, lane_y)
        self.turn_bias = 1.0 if info.unit_id % 2 == 0 else -1.0

    def _path_is_clear(self, position: tuple[float, float], angle: float) -> bool:
        """Check a short ray against wall cells this unit has actually observed."""

        for step in range(1, 9):
            distance = 3.0 * step / 8.0
            cell = (
                int(position[0] + math.cos(angle) * distance),
                int(position[1] + math.sin(angle) * distance),
            )
            if cell in self.known_walls:
                return False
        return True

    def _velocity_toward(
        self,
        observation: Observation,
        destination: tuple[float, float],
    ) -> tuple[float, float]:
        position = observation.self_state.position
        preferred = math.atan2(destination[1] - position[1], destination[0] - position[0])
        offsets = (0.0, self.turn_bias * math.pi / 4.0, -self.turn_bias * math.pi / 4.0)
        if self.stuck_ticks >= 8:
            offsets = (self.turn_bias * math.pi / 2.0, -self.turn_bias * math.pi / 2.0, math.pi)
        travel_angle = next(
            (preferred + offset for offset in offsets if self._path_is_clear(position, preferred + offset)),
            preferred + offsets[-1],
        )

        speed = self.info.rules.max_speed
        vx, vy = math.cos(travel_angle) * speed, math.sin(travel_angle) * speed

        # A small local repulsion keeps independently controlled allies from bunching.
        for friendly in observation.visible_friendlies:
            distance = math.dist(position, friendly.position)
            if 1e-6 < distance < 1.5:
                weight = (1.5 - distance) / distance
                vx += (position[0] - friendly.position[0]) * weight
                vy += (position[1] - friendly.position[1]) * weight

        magnitude = math.hypot(vx, vy)
        if magnitude > speed:
            vx, vy = vx * speed / magnitude, vy * speed / magnitude
        return vx, vy

    def step(self, observation: Observation) -> Action:
        for cell in observation.visible_terrain:
            if cell.blocked:
                self.known_walls.add((cell.x, cell.y))

        position = observation.self_state.position
        if math.dist(position, self.last_position) < 0.025:
            self.stuck_ticks += 1
        else:
            self.stuck_ticks = 0
        self.last_position = position

        enemy = min(
            observation.visible_enemies,
            key=lambda unit: (math.dist(position, unit.position), unit.unit_id),
            default=None,
        )
        destination = enemy.position if enemy is not None else self.goal
        desired_heading = math.atan2(destination[1] - position[1], destination[0] - position[0])
        desired_velocity = self._velocity_toward(observation, destination)

        can_fire = False
        if enemy is not None:
            can_fire = (
                math.dist(position, enemy.position) <= self.info.rules.weapon_range
                and abs(angle_error(observation.self_state.heading, desired_heading)) <= math.radians(4.0)
                and not friend_blocks_shot(position, enemy.position, observation.visible_friendlies)
            )

        ammunition = observation.self_state.ammunition
        reload = ammunition == 0 or (ammunition <= 2 and enemy is None)
        return Action(
            desired_velocity=desired_velocity,
            desired_heading=desired_heading,
            fire=can_fire,
            reload=reload,
            broadcast=None,
        )
