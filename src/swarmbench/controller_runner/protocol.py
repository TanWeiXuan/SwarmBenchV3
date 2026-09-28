"""Bounded, versioned JSON codecs for the untrusted worker boundary."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from swarmbench.api import Observation, RadioMessage, SelfState, Team, TerrainCell, UnitInfo, VisibleUnit
from swarmbench.rules import Rules
from swarmbench.version import PROTOCOL_VERSION


class ProtocolError(RuntimeError):
    pass


def _self_from(data: dict[str, Any]) -> SelfState:
    return SelfState(
        int(data["unit_id"]),
        (float(data["position"][0]), float(data["position"][1])),
        (float(data["velocity"][0]), float(data["velocity"][1])),
        float(data["heading"]),
        int(data["health"]),
        int(data["ammunition"]),
        bool(data["reloading"]),
        float(data["reload_remaining"]),
        float(data["cooldown_remaining"]),
    )


def info_to_dict(info: UnitInfo) -> dict[str, Any]:
    return {
        "rules": info.rules.to_dict(),
        "arena_size": list(info.arena_size),
        "team": info.team.value,
        "unit_id": info.unit_id,
        "initial_team_size": info.initial_team_size,
        "starting_state": asdict(info.starting_state),
        "controller_seed": info.controller_seed,
    }


def info_from_dict(data: dict[str, Any]) -> UnitInfo:
    return UnitInfo(
        Rules(**data["rules"]),
        (int(data["arena_size"][0]), int(data["arena_size"][1])),
        Team(data["team"]),
        int(data["unit_id"]),
        int(data["initial_team_size"]),
        _self_from(data["starting_state"]),
        int(data["controller_seed"]),
    )


def observation_to_dict(observation: Observation) -> dict[str, Any]:
    return asdict(observation)


def observation_from_dict(data: dict[str, Any]) -> Observation:
    def visible(item: dict[str, Any]) -> VisibleUnit:
        return VisibleUnit(
            int(item["unit_id"]), bool(item["friendly"]),
            (float(item["position"][0]), float(item["position"][1])),
            (float(item["velocity"][0]), float(item["velocity"][1])), float(item["heading"]),
        )

    return Observation(
        int(data["tick"]),
        float(data["time"]),
        _self_from(data["self_state"]),
        tuple(visible(item) for item in data["visible_friendlies"]),
        tuple(visible(item) for item in data["visible_enemies"]),
        tuple(TerrainCell(int(item["x"]), int(item["y"]), bool(item["blocked"])) for item in data["visible_terrain"]),
        tuple(RadioMessage(int(item["sender_id"]), int(item["payload"])) for item in data["radio"]),
    )


def request(sequence: int, tick: int, command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"protocol_version": PROTOCOL_VERSION, "sequence": sequence, "tick": tick, "command": command, "payload": payload}


def validate_request(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict) or message.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("invalid protocol version")
    if type(message.get("sequence")) is not int or message["sequence"] < 0:
        raise ProtocolError("invalid sequence")
    if type(message.get("tick")) is not int or message["tick"] < -1:
        raise ProtocolError("invalid tick")
    if message.get("command") not in {"initialize", "step", "shutdown"} or not isinstance(message.get("payload"), dict):
        raise ProtocolError("invalid request")
    return message


def validate_response(message: Any, sequence: int, tick: int) -> dict[str, Any]:
    if not isinstance(message, dict) or message.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError("invalid response version")
    if message.get("sequence") != sequence or message.get("tick") != tick:
        raise ProtocolError("stale or mismatched response")
    if message.get("status") not in {"ok", "error"}:
        raise ProtocolError("invalid response status")
    return message
