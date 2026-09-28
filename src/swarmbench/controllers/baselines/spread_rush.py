from swarmbench import BaseUnitController, Observation, UnitInfo
from swarmbench.controllers.baselines.common import SimpleNavigator


class UnitController(SimpleNavigator, BaseUnitController):
    def initialize(self, info: UnitInfo) -> None:
        self.initialize_common(info, spread=True)

    def step(self, observation: Observation):
        return self.combat_action(observation, self.base_goal)
