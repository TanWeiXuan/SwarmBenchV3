"""Minimal legal one-file SwarmBenchV3 submission."""

from swarmbench import Action, BaseUnitController, Observation, Team, UnitInfo


class UnitController(BaseUnitController):
    def initialize(self, info: UnitInfo) -> None:
        self.direction = 1.0 if info.team is Team.A else -1.0

    def step(self, observation: Observation) -> Action:
        return Action(
            desired_velocity=(2.0 * self.direction, 0.0),
            desired_heading=0.0 if self.direction > 0 else 3.141592653589793,
            fire=False,
            reload=observation.self_state.ammunition == 0,
            broadcast=None,
        )
