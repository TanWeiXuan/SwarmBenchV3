"""Spread rush plus an optional eight-byte recent-contact protocol."""

from __future__ import annotations

import struct

from swarmbench import BaseUnitController, Observation, UnitInfo
from swarmbench.controllers.baselines.common import SimpleNavigator

PACKET = struct.Struct("<BBHHH")
CONTACT = 1
MAX_REPORT_AGE = 30


def pack_contact(enemy_id: int, x: float, y: float, tick: int) -> int:
    if not 0 <= enemy_id < 256 or not 0 <= x <= 120 or not 0 <= y <= 80 or not 0 <= tick < 65536:
        raise ValueError("contact is outside the baseline packet range")
    return int.from_bytes(PACKET.pack(CONTACT, enemy_id, round(x * 100), round(y * 100), tick), "little")


def unpack_contact(payload: int, current_tick: int) -> tuple[int, float, float, int] | None:
    if type(payload) is not int or not 0 <= payload <= 2**64 - 1:
        return None
    kind, enemy_id, x_cm, y_cm, tick = PACKET.unpack(payload.to_bytes(8, "little"))
    if kind != CONTACT or x_cm > 12000 or y_cm > 8000 or tick > current_tick or current_tick - tick > MAX_REPORT_AGE:
        return None
    return enemy_id, x_cm / 100.0, y_cm / 100.0, tick


class UnitController(SimpleNavigator, BaseUnitController):
    def initialize(self, info: UnitInfo) -> None:
        self.initialize_common(info, spread=True)
        self.report = None

    def step(self, observation: Observation):
        packet = None
        if observation.visible_enemies:
            enemy = min(observation.visible_enemies, key=lambda item: item.unit_id)
            self.report = (enemy.unit_id, enemy.position[0], enemy.position[1], observation.tick)
            packet = pack_contact(*self.report)
        for message in observation.radio:
            report = unpack_contact(message.payload, observation.tick)
            if report is not None and (self.report is None or report[3] > self.report[3]):
                self.report = report
        destination = self.base_goal
        if self.report is not None and observation.tick - self.report[3] <= MAX_REPORT_AGE:
            destination = self.report[1], self.report[2]
        return self.combat_action(observation, destination, broadcast=packet)
