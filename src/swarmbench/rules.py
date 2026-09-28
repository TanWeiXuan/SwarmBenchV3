"""The one versioned ruleset used by official SwarmBenchV3 matches."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .version import RULESET_VERSION


@dataclass(frozen=True, slots=True)
class Rules:
    version: str = RULESET_VERSION
    teams: int = 2
    units_per_team: int = 8
    match_seconds: float = 120.0
    arena_width: int = 120
    arena_height: int = 80
    cell_size: float = 1.0
    unit_radius: float = 0.4
    max_speed: float = 4.0
    max_acceleration: float = 8.0
    physics_hz: int = 20
    controller_hz: int = 10
    visual_range: float = 18.0
    field_of_view: float = 2.0943951023931953
    close_awareness: float = 3.0
    max_heading_rate: float = 3.141592653589793
    weapon_range: float = 22.0
    max_health: int = 100
    damage: int = 20
    fire_interval: float = 0.8
    magazine_size: int = 6
    reload_seconds: float = 2.0
    radio_range: float = 30.0
    radio_latency_steps: int = 1
    max_radio_payload: int = 2**64 - 1
    collision_tolerance: float = 1e-9
    soft_step_deadline: float = 0.1
    hard_step_deadline: float = 1.0
    initialization_deadline: float = 10.0
    worker_memory_mib: int = 256
    worker_scratch_mib: int = 16
    worker_process_limit: int = 64
    max_protocol_bytes: int = 1_048_576
    max_log_bytes: int = 65_536

    @property
    def physics_dt(self) -> float:
        return 1.0 / self.physics_hz

    @property
    def control_dt(self) -> float:
        return 1.0 / self.controller_hz

    @property
    def physics_steps_per_control(self) -> int:
        return self.physics_hz // self.controller_hz

    @property
    def match_control_ticks(self) -> int:
        return round(self.match_seconds * self.controller_hz)

    @property
    def fire_interval_ticks(self) -> int:
        return round(self.fire_interval * self.controller_hz)

    @property
    def reload_ticks(self) -> int:
        return round(self.reload_seconds * self.controller_hz)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


OFFICIAL_RULES = Rules()
