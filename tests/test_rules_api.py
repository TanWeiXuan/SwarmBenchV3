from dataclasses import FrozenInstanceError

import pytest

from swarmbench import Action, OFFICIAL_RULES
from swarmbench.engine import validate_action


def test_official_rules_are_exact_and_immutable() -> None:
    rules = OFFICIAL_RULES
    assert (rules.units_per_team, rules.match_seconds, rules.arena_width, rules.arena_height) == (8, 120.0, 120, 80)
    assert (rules.physics_hz, rules.controller_hz, rules.match_control_ticks) == (20, 10, 1200)
    assert rules.magazine_size == 6 and rules.reload_ticks == 20 and rules.fire_interval_ticks == 8
    with pytest.raises(FrozenInstanceError):
        rules.max_speed = 9  # type: ignore[misc]


def test_action_validation_retains_missing_setpoints_and_rejects_bool_message() -> None:
    value = validate_action({"fire": True})
    assert value.desired_velocity is None and value.desired_heading is None and value.fire
    assert validate_action(Action(broadcast=0)).broadcast == 0
    assert validate_action(Action(broadcast=2**64 - 1)).broadcast == 2**64 - 1
    assert validate_action({"broadcast": True}).broadcast is None
    assert validate_action({"broadcast": -1}).broadcast is None
    assert validate_action({"broadcast": 2**64}).broadcast is None
    assert validate_action({"unknown": 4}).invalid_fields == 1
