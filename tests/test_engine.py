import math

import pytest

from swarmbench import Action, Team
from swarmbench.engine import Simulation

from .helpers import isolated_simulation, revive, scenario_with_walls


def test_acceleration_speed_and_heading_limits() -> None:
    simulation = Simulation(scenario_with_walls())
    simulation.step({(Team.A, 0): Action((99.0, 0.0), math.pi)})
    unit = simulation.units[(Team.A, 0)]
    assert unit.velocity[0] == pytest.approx(0.8, abs=1e-9)
    assert unit.heading == pytest.approx(math.pi / 10, abs=1e-9)  # exact pi tie turns CCW
    for _ in range(10):
        simulation.step({})
    assert math.hypot(*unit.velocity) <= 4.0 + 1e-9


def test_wall_collision_slides_without_tunneling() -> None:
    simulation = isolated_simulation({(12, y) for y in range(1, 79)})
    unit = revive(simulation, Team.A, 0, (11.5, 20.5))
    revive(simulation, Team.B, 0, (100.5, 20.5), math.pi)
    unit.velocity = unit.desired_velocity = (4.0, 1.0)
    simulation.step({})
    assert unit.position[0] <= 11.600000001
    assert unit.position[1] > 20.5


def test_friendly_enemy_collision_and_swap_through_are_symmetric() -> None:
    for second_team in (Team.A, Team.B):
        simulation = isolated_simulation()
        left = revive(simulation, Team.A, 0, (20.0, 20.0))
        right = revive(simulation, second_team, 1 if second_team is Team.A else 0, (20.9, 20.0), math.pi)
        if second_team is Team.A:
            revive(simulation, Team.B, 0, (100.0, 20.0), math.pi)
        left.velocity = left.desired_velocity = (4.0, 0.0)
        right.velocity = right.desired_velocity = (-4.0, 0.0)
        simulation.step({})
        assert left.position[0] < right.position[0]
        assert math.dist(left.position, right.position) >= 0.8 - 1e-9
        assert left.alive and right.alive


def test_wall_shielding_and_nearest_friendly_interception() -> None:
    wall_sim = isolated_simulation({(15, 20)})
    shooter = revive(wall_sim, Team.A, 0, (10.0, 20.5))
    target = revive(wall_sim, Team.B, 0, (20.0, 20.5), math.pi)
    wall_sim.step({shooter.key: Action(fire=True)})
    assert target.health == 100

    simulation = isolated_simulation()
    shooter = revive(simulation, Team.A, 0, (10.0, 20.0))
    friendly = revive(simulation, Team.A, 1, (15.0, 20.0))
    revive(simulation, Team.B, 0, (20.0, 20.0), math.pi)
    simulation.step({shooter.key: Action(fire=True)})
    assert friendly.health == 80
    assert simulation.events[-2]["type"] in {"shot", "damage"}
    assert any(event.get("friendly_fire") for event in simulation.events if event["type"] == "shot")


def test_simultaneous_mutual_elimination_is_draw() -> None:
    simulation = isolated_simulation()
    left = revive(simulation, Team.A, 0, (10.0, 20.0))
    right = revive(simulation, Team.B, 0, (20.0, 20.0), math.pi)
    left.health = right.health = 20
    simulation.step({left.key: Action(fire=True), right.key: Action(fire=True)})
    assert simulation.result() == (None, "simultaneous_elimination")
    assert not left.alive and not right.alive
    assert simulation.tick == 0 and simulation.time == 0.0


def test_new_heading_does_not_rotate_a_same_boundary_shot() -> None:
    simulation = isolated_simulation()
    shooter = revive(simulation, Team.A, 0, (10.0, 20.0), math.pi / 2)
    target = revive(simulation, Team.B, 0, (20.0, 20.0), math.pi)
    simulation.step({shooter.key: Action(desired_heading=0.0, fire=True)})
    assert target.health == 100
    for _ in range(5):
        simulation.step({shooter.key: Action(desired_heading=0.0)})
    shooter.next_fire_tick = simulation.tick
    simulation.step({shooter.key: Action(fire=True)})
    assert target.health == 80


def test_fire_interval_magazine_and_reload_semantics() -> None:
    simulation = isolated_simulation()
    shooter = revive(simulation, Team.A, 0, (10.0, 20.0))
    target = revive(simulation, Team.B, 0, (20.0, 20.0), math.pi)
    for _ in range(7):
        simulation.step({shooter.key: Action(fire=True)})
    assert target.health == 80 and shooter.ammunition == 5
    simulation.step({shooter.key: Action(fire=True)})
    simulation.step({shooter.key: Action(fire=True)})
    assert target.health == 60
    shooter.ammunition = 1
    shooter.next_fire_tick = simulation.tick
    simulation.step({shooter.key: Action(fire=True)})
    assert shooter.ammunition == 0 and shooter.reload_complete_tick == simulation.tick - 1 + 20
    for _ in range(19):
        simulation.step({})
    simulation.observations()
    assert shooter.ammunition == 6 and shooter.reload_complete_tick is None


def test_reload_request_precedes_fire_and_full_reload_is_noop() -> None:
    simulation = isolated_simulation()
    shooter = revive(simulation, Team.A, 0, (10.0, 20.0))
    target = revive(simulation, Team.B, 0, (20.0, 20.0), math.pi)
    simulation.step({shooter.key: Action(fire=True, reload=True)})
    assert target.health == 80 and shooter.reload_complete_tick is None
    shooter.ammunition = 3
    simulation.step({shooter.key: Action(fire=True, reload=True)})
    assert target.health == 80 and shooter.reload_complete_tick is not None


def test_time_limit_uses_only_survivor_count() -> None:
    simulation = Simulation(scenario_with_walls())
    simulation.units[(Team.A, 0)].health = 1
    simulation.tick = 1200
    assert simulation.result() == (None, "time_limit_equal_survivors")
    simulation.units[(Team.B, 0)].alive = False
    assert simulation.result() == (Team.A, "time_limit_survivors")


def test_exact_match_duration_has_no_extra_boundary() -> None:
    simulation = Simulation(scenario_with_walls())
    for _ in range(1200):
        simulation.step({})
    assert simulation.tick == 1200 and simulation.time == 120.0
    assert simulation.result() == (None, "time_limit_equal_survivors")
    with pytest.raises(RuntimeError, match="time limit"):
        simulation.step({})
