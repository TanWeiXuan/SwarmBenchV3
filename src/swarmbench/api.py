"""Small immutable public API presented independently to each unit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from .rules import Rules

Vec2: TypeAlias = tuple[float, float]


class Team(str, Enum):
    A = "A"
    B = "B"

    @property
    def opponent(self) -> "Team":
        return Team.B if self is Team.A else Team.A


@dataclass(frozen=True, slots=True)
class SelfState:
    unit_id: int
    position: Vec2
    velocity: Vec2
    heading: float
    health: int
    ammunition: int
    reloading: bool
    reload_remaining: float
    cooldown_remaining: float


@dataclass(frozen=True, slots=True)
class VisibleUnit:
    unit_id: int
    friendly: bool
    position: Vec2
    velocity: Vec2
    heading: float


@dataclass(frozen=True, slots=True)
class TerrainCell:
    x: int
    y: int
    blocked: bool


@dataclass(frozen=True, slots=True)
class RadioMessage:
    sender_id: int
    payload: int


@dataclass(frozen=True, slots=True)
class Observation:
    tick: int
    time: float
    self_state: SelfState
    visible_friendlies: tuple[VisibleUnit, ...]
    visible_enemies: tuple[VisibleUnit, ...]
    visible_terrain: tuple[TerrainCell, ...]
    radio: tuple[RadioMessage, ...]


@dataclass(frozen=True, slots=True)
class UnitInfo:
    rules: Rules
    arena_size: tuple[int, int]
    team: Team
    unit_id: int
    initial_team_size: int
    starting_state: SelfState
    controller_seed: int


@dataclass(frozen=True, slots=True)
class Action:
    desired_velocity: Vec2 | None = None
    desired_heading: float | None = None
    fire: bool = False
    reload: bool = False
    broadcast: int | None = None


class BaseUnitController:
    def initialize(self, info: UnitInfo) -> None:
        """Called once at the start of one match for one unit."""

    def step(self, observation: Observation) -> Action:
        return Action(desired_heading=observation.self_state.heading)
