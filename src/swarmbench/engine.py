"""Authoritative headless V3 simulation and exact control-boundary ordering."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, TypeAlias

from .api import Action, Observation, RadioMessage, SelfState, Team, TerrainCell, VisibleUnit
from .arena import Scenario
from .geometry import (
    EPS,
    approach_vector,
    clamp_norm,
    first_wall_hit,
    normalize_angle,
    ray_circle,
    shortest_angle_delta,
    slide_move,
    swept_pair_distance,
    unit_visible,
    visible_terrain,
)
from .rules import OFFICIAL_RULES, Rules

UnitKey: TypeAlias = tuple[Team, int]


@dataclass(slots=True)
class Unit:
    team: Team
    unit_id: int
    position: tuple[float, float]
    velocity: tuple[float, float]
    heading: float
    health: int
    ammunition: int
    desired_velocity: tuple[float, float]
    desired_heading: float
    next_fire_tick: int = 0
    reload_complete_tick: int | None = None
    alive: bool = True

    @property
    def key(self) -> UnitKey:
        return self.team, self.unit_id


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    desired_velocity: tuple[float, float] | None
    desired_heading: float | None
    fire: bool
    reload: bool
    broadcast: int | None
    invalid_fields: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "desired_velocity": list(self.desired_velocity) if self.desired_velocity is not None else None,
            "desired_heading": self.desired_heading,
            "fire": self.fire,
            "reload": self.reload,
            "broadcast": self.broadcast,
            "invalid_fields": self.invalid_fields,
        }


def validate_action(raw: Any, rules: Rules = OFFICIAL_RULES) -> ValidatedAction:
    if raw is None:
        return ValidatedAction(None, None, False, False, None)
    if isinstance(raw, Action):
        data = asdict(raw)
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        return ValidatedAction(None, None, False, False, None, 1)
    invalid = len(set(data) - {"desired_velocity", "desired_heading", "fire", "reload", "broadcast"})
    velocity = data.get("desired_velocity")
    accepted_velocity = None
    if velocity is not None:
        if (
            not isinstance(velocity, (list, tuple))
            or len(velocity) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in velocity)
        ):
            invalid += 1
        else:
            accepted_velocity = clamp_norm((float(velocity[0]), float(velocity[1])), rules.max_speed)
    heading = data.get("desired_heading")
    accepted_heading = None
    if heading is not None:
        if isinstance(heading, bool) or not isinstance(heading, (int, float)) or not math.isfinite(float(heading)):
            invalid += 1
        else:
            accepted_heading = normalize_angle(float(heading))
    fire = data.get("fire", False)
    reload_request = data.get("reload", False)
    if type(fire) is not bool:
        invalid += 1
        fire = False
    if type(reload_request) is not bool:
        invalid += 1
        reload_request = False
    broadcast = data.get("broadcast")
    if broadcast is not None and (type(broadcast) is not int or not 0 <= broadcast <= rules.max_radio_payload):
        invalid += 1
        broadcast = None
    return ValidatedAction(accepted_velocity, accepted_heading, fire, reload_request, broadcast, invalid)


class Simulation:
    def __init__(self, scenario: Scenario, rules: Rules = OFFICIAL_RULES) -> None:
        self.scenario = scenario
        self.rules = rules
        self.tick = 0
        self.units: dict[UnitKey, Unit] = {}
        for team in (Team.A, Team.B):
            heading = 0.0 if team is Team.A else math.pi
            for unit_id, position in enumerate(scenario.spawns(team)):
                self.units[(team, unit_id)] = Unit(
                    team,
                    unit_id,
                    position,
                    (0.0, 0.0),
                    heading,
                    rules.max_health,
                    rules.magazine_size,
                    (0.0, 0.0),
                    heading,
                )
        self.events: list[dict[str, Any]] = []
        self.invalid_actions = {Team.A: 0, Team.B: 0}
        self._pending_messages: dict[int, dict[UnitKey, list[RadioMessage]]] = {}
        self._boundary_inboxes: dict[UnitKey, tuple[RadioMessage, ...]] = {}
        self._prepared_tick: int | None = None
        self.physics_frames: list[dict[str, Any]] = [self.state_frame(0.0)]

    @property
    def time(self) -> float:
        return self.tick * self.rules.control_dt

    def living(self, team: Team | None = None) -> list[Unit]:
        return [unit for unit in self.units.values() if unit.alive and (team is None or unit.team is team)]

    def _complete_timers(self) -> None:
        if self._prepared_tick == self.tick:
            return
        for unit in self.living():
            if unit.reload_complete_tick is not None and unit.reload_complete_tick <= self.tick:
                unit.ammunition = self.rules.magazine_size
                unit.reload_complete_tick = None
                self.events.append({"tick": self.tick, "type": "reload_complete", "team": unit.team.value, "unit_id": unit.unit_id})
        eligible = self._pending_messages.pop(self.tick, {})
        self._boundary_inboxes = {}
        for key, messages in eligible.items():
            if not self.units[key].alive:
                continue
            ordered = tuple(sorted(messages, key=lambda item: item.sender_id))
            self._boundary_inboxes[key] = ordered
            for message in ordered:
                self.events.append({
                    "tick": self.tick,
                    "type": "radio_deliver",
                    "team": key[0].value,
                    "unit_id": key[1],
                    "sender_id": message.sender_id,
                    "payload": message.payload,
                })
        self._prepared_tick = self.tick

    def self_state(self, unit: Unit) -> SelfState:
        remaining = 0.0 if unit.reload_complete_tick is None else max(0, unit.reload_complete_tick - self.tick) * self.rules.control_dt
        cooldown = max(0, unit.next_fire_tick - self.tick) * self.rules.control_dt
        return SelfState(unit.unit_id, unit.position, unit.velocity, unit.heading, unit.health, unit.ammunition, unit.reload_complete_tick is not None, remaining, cooldown)

    def observation(self, key: UnitKey) -> Observation:
        self._complete_timers()
        observer = self.units[key]
        if not observer.alive:
            raise ValueError("dead units do not receive observations")
        friends: list[VisibleUnit] = []
        enemies: list[VisibleUnit] = []
        for other in sorted(self.living(), key=lambda item: (item.team.value, item.unit_id)):
            if other.key == key or not unit_visible(
                self.scenario.arena,
                observer.position,
                observer.heading,
                other.position,
                self.rules.visual_range,
                self.rules.field_of_view,
                self.rules.close_awareness,
            ):
                continue
            visible = VisibleUnit(other.unit_id, other.team is observer.team, other.position, other.velocity, other.heading)
            (friends if visible.friendly else enemies).append(visible)
        terrain = tuple(
            TerrainCell(x, y, blocked)
            for x, y, blocked in visible_terrain(
                self.scenario.arena,
                observer.position,
                observer.heading,
                self.rules.visual_range,
                self.rules.field_of_view,
                self.rules.close_awareness,
            )
        )
        return Observation(
            self.tick,
            self.time,
            self.self_state(observer),
            tuple(friends),
            tuple(enemies),
            terrain,
            self._boundary_inboxes.get(key, ()),
        )

    def observations(self) -> dict[UnitKey, Observation]:
        self._complete_timers()
        return {unit.key: self.observation(unit.key) for unit in self.living()}

    def _queue_radios(self, actions: dict[UnitKey, ValidatedAction], boundary_units: tuple[Unit, ...]) -> None:
        delivery = self._pending_messages.setdefault(self.tick + self.rules.radio_latency_steps, {})
        for sender in boundary_units:
            payload = actions.get(sender.key, ValidatedAction(None, None, False, False, None)).broadcast
            if payload is None:
                continue
            recipients: list[int] = []
            for receiver in boundary_units:
                if receiver.team is not sender.team or receiver.unit_id == sender.unit_id:
                    continue
                if math.dist(sender.position, receiver.position) <= self.rules.radio_range + EPS:
                    delivery.setdefault(receiver.key, []).append(RadioMessage(sender.unit_id, payload))
                    recipients.append(receiver.unit_id)
            self.events.append({
                "tick": self.tick,
                "type": "radio_transmit",
                "team": sender.team.value,
                "unit_id": sender.unit_id,
                "payload": payload,
                "recipients": recipients,
                "delivery_tick": self.tick + 1,
            })

    def _resolve_volley(self, actions: dict[UnitKey, ValidatedAction], boundary_units: tuple[Unit, ...]) -> None:
        damage: dict[UnitKey, int] = {}
        snapshot = {unit.key: (unit.position, unit.heading) for unit in boundary_units}
        for shooter in boundary_units:
            action = actions.get(shooter.key, ValidatedAction(None, None, False, False, None))
            if action.reload and shooter.ammunition < self.rules.magazine_size and shooter.reload_complete_tick is None:
                shooter.reload_complete_tick = self.tick + self.rules.reload_ticks
                self.events.append({"tick": self.tick, "type": "reload_start", "team": shooter.team.value, "unit_id": shooter.unit_id, "automatic": False})
                continue
            if not action.fire or shooter.reload_complete_tick is not None or shooter.ammunition <= 0 or self.tick < shooter.next_fire_tick:
                continue
            origin, heading = snapshot[shooter.key]
            direction = math.cos(heading), math.sin(heading)
            wall = first_wall_hit(self.scenario.arena, origin, direction, self.rules.weapon_range)
            wall_distance = wall[0] if wall else math.inf
            candidates: list[tuple[float, str, int, UnitKey]] = []
            for target in boundary_units:
                if target.key == shooter.key:
                    continue
                hit = ray_circle(origin, direction, snapshot[target.key][0], self.rules.unit_radius)
                if hit is not None and hit <= self.rules.weapon_range + EPS:
                    candidates.append((hit, target.team.value, target.unit_id, target.key))
            candidates.sort()
            target_key = None if not candidates or wall_distance <= candidates[0][0] + EPS else candidates[0][3]
            distance = min(wall_distance, candidates[0][0] if candidates else math.inf, self.rules.weapon_range)
            endpoint = origin[0] + direction[0] * distance, origin[1] + direction[1] * distance
            shooter.ammunition -= 1
            shooter.next_fire_tick = self.tick + self.rules.fire_interval_ticks
            if shooter.ammunition == 0:
                shooter.reload_complete_tick = self.tick + self.rules.reload_ticks
                self.events.append({"tick": self.tick, "type": "reload_start", "team": shooter.team.value, "unit_id": shooter.unit_id, "automatic": True})
            if target_key is not None:
                damage[target_key] = damage.get(target_key, 0) + self.rules.damage
            self.events.append({
                "tick": self.tick,
                "type": "shot",
                "team": shooter.team.value,
                "unit_id": shooter.unit_id,
                "origin": list(origin),
                "endpoint": list(endpoint),
                "hit": None if target_key is None else {"team": target_key[0].value, "unit_id": target_key[1]},
                "friendly_fire": target_key is not None and target_key[0] is shooter.team,
            })
        for key, amount in sorted(damage.items(), key=lambda item: (item[0][0].value, item[0][1])):
            unit = self.units[key]
            unit.health = max(0, unit.health - amount)
            self.events.append({"tick": self.tick, "type": "damage", "team": unit.team.value, "unit_id": unit.unit_id, "amount": amount, "health": unit.health})
        for unit in boundary_units:
            if unit.health <= 0:
                unit.alive = False
                unit.velocity = (0.0, 0.0)
                self.events.append({"tick": self.tick, "type": "death", "team": unit.team.value, "unit_id": unit.unit_id, "position": list(unit.position)})

    def _integrate_substep(self, frame_time: float) -> None:
        dt = self.rules.physics_dt
        living = tuple(sorted(self.living(), key=lambda item: (item.team.value, item.unit_id)))
        starts = {unit.key: unit.position for unit in living}
        candidates: dict[UnitKey, tuple[float, float]] = {}
        proposed_velocity: dict[UnitKey, tuple[float, float]] = {}
        for unit in living:
            velocity = approach_vector(unit.velocity, unit.desired_velocity, self.rules.max_acceleration * dt)
            velocity = clamp_norm(velocity, self.rules.max_speed)
            proposed_velocity[unit.key] = velocity
            raw = unit.position[0] + velocity[0] * dt, unit.position[1] + velocity[1] * dt
            candidates[unit.key] = slide_move(self.scenario.arena, unit.position, raw, self.rules.unit_radius)
            delta = shortest_angle_delta(unit.heading, unit.desired_heading)
            turn = max(-self.rules.max_heading_rate * dt, min(self.rules.max_heading_rate * dt, delta))
            unit.heading = normalize_angle(unit.heading + turn)
        blocked: set[UnitKey] = set()
        diameter = 2 * self.rules.unit_radius
        for index, left in enumerate(living):
            for right in living[index + 1 :]:
                if swept_pair_distance(starts[left.key], candidates[left.key], starts[right.key], candidates[right.key]) < diameter - self.rules.collision_tolerance:
                    blocked.update((left.key, right.key))
        for unit in living:
            end = starts[unit.key] if unit.key in blocked else candidates[unit.key]
            unit.position = end
            unit.velocity = ((end[0] - starts[unit.key][0]) / dt, (end[1] - starts[unit.key][1]) / dt)
        self.physics_frames.append(self.state_frame(frame_time))

    def step(self, raw_actions: dict[UnitKey, Any]) -> dict[UnitKey, ValidatedAction]:
        if self.tick >= self.rules.match_control_ticks:
            raise RuntimeError("match has reached its time limit")
        self._complete_timers()
        boundary_units = tuple(sorted(self.living(), key=lambda item: (item.team.value, item.unit_id)))
        actions = {key: validate_action(raw_actions.get(key), self.rules) for key in (unit.key for unit in boundary_units)}
        for (team, _), action in actions.items():
            self.invalid_actions[team] += action.invalid_fields
        for unit in boundary_units:
            action = actions[unit.key]
            if action.desired_velocity is not None:
                unit.desired_velocity = action.desired_velocity
            if action.desired_heading is not None:
                unit.desired_heading = action.desired_heading
        self._queue_radios(actions, boundary_units)
        self._resolve_volley(actions, boundary_units)
        if not self.living(Team.A) or not self.living(Team.B):
            # Elimination is adjudicated at this boundary; there is no hidden extra 0.1 s.
            self.physics_frames.append(self.state_frame(self.time))
            return actions
        for substep in range(self.rules.physics_steps_per_control):
            self._integrate_substep(self.time + (substep + 1) * self.rules.physics_dt)
        self.tick += 1
        self._prepared_tick = None
        return actions

    def result(self) -> tuple[Team | None, str] | None:
        alive_a, alive_b = len(self.living(Team.A)), len(self.living(Team.B))
        if not alive_a or not alive_b:
            if not alive_a and not alive_b:
                return None, "simultaneous_elimination"
            return (Team.A if alive_a else Team.B), "elimination"
        if self.tick >= self.rules.match_control_ticks:
            if alive_a == alive_b:
                return None, "time_limit_equal_survivors"
            return (Team.A if alive_a > alive_b else Team.B), "time_limit_survivors"
        return None

    def state_frame(self, time_value: float | None = None) -> dict[str, Any]:
        return {
            "time": self.time if time_value is None else round(time_value, 10),
            "units": [
                {
                    "team": unit.team.value,
                    "unit_id": unit.unit_id,
                    "position": [round(unit.position[0], 10), round(unit.position[1], 10)],
                    "velocity": [round(unit.velocity[0], 10), round(unit.velocity[1], 10)],
                    "heading": round(unit.heading, 10),
                    "health": unit.health,
                    "ammunition": unit.ammunition,
                    "desired_velocity": [round(unit.desired_velocity[0], 10), round(unit.desired_velocity[1], 10)],
                    "desired_heading": round(unit.desired_heading, 10),
                    "next_fire_tick": unit.next_fire_tick,
                    "reload_complete_tick": unit.reload_complete_tick,
                    "alive": unit.alive,
                }
                for unit in sorted(self.units.values(), key=lambda item: (item.team.value, item.unit_id))
            ],
        }

    def canonical_hash(self) -> str:
        payload = json.dumps(self.state_frame(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()
