from __future__ import annotations

from swarmbench.api import Team
from swarmbench.arena import Arena, Scenario
from swarmbench.engine import Simulation
from swarmbench.version import GENERATOR_VERSION


def scenario_with_walls(walls: set[tuple[int, int]] | None = None) -> Scenario:
    walls = walls or set()
    rows = []
    for y in range(80):
        row = []
        for x in range(120):
            row.append("#" if x in {0, 119} or y in {0, 79} or (x, y) in walls else ".")
        rows.append("".join(row))
    a = tuple((5.5, 8.5 + unit_id * 8.0) for unit_id in range(8))
    b = tuple((114.5, 8.5 + unit_id * 8.0) for unit_id in range(8))
    return Scenario(1, GENERATOR_VERSION, Arena(120, 80, tuple(rows)), a, b)


def isolated_simulation(walls: set[tuple[int, int]] | None = None) -> Simulation:
    simulation = Simulation(scenario_with_walls(walls))
    for unit in simulation.units.values():
        unit.alive = False
    return simulation


def revive(simulation: Simulation, team: Team, unit_id: int, position: tuple[float, float], heading: float = 0.0):
    unit = simulation.units[(team, unit_id)]
    unit.alive = True
    unit.health = 100
    unit.position = position
    unit.velocity = (0.0, 0.0)
    unit.desired_velocity = (0.0, 0.0)
    unit.heading = heading
    unit.desired_heading = heading
    unit.ammunition = 6
    unit.next_fire_tick = 0
    unit.reload_complete_tick = None
    return unit
