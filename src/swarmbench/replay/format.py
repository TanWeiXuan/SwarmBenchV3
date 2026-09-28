"""Versioned bounded authoritative replay files and action reconstruction."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarmbench.api import Team
from swarmbench.arena import Scenario
from swarmbench.engine import Simulation
from swarmbench.rules import OFFICIAL_RULES, Rules
from swarmbench.version import ENGINE_VERSION, REPLAY_FORMAT_VERSION

MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 128 * 1024 * 1024


class ReplayValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Replay:
    metadata: dict[str, Any]
    rules: Rules
    scenario: Scenario
    controllers: dict[str, dict[str, str]]
    accepted_actions: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    frames: tuple[dict[str, Any], ...]
    observation_hashes: tuple[dict[str, str], ...]
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "replay_format_version": REPLAY_FORMAT_VERSION,
            "engine_version": ENGINE_VERSION,
            "metadata": self.metadata,
            "rules": self.rules.to_dict(),
            "scenario": self.scenario.to_dict(),
            "controllers": self.controllers,
            "accepted_actions": list(self.accepted_actions),
            "events": list(self.events),
            "frames": list(self.frames),
            "observation_hashes": list(self.observation_hashes),
            "result": self.result,
        }


def observation_hash(observation: Any) -> str:
    from dataclasses import asdict

    payload = json.dumps(asdict(observation), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()[:24]


def _bounded_strings(value: Any) -> None:
    if isinstance(value, str) and len(value) > 4096:
        raise ReplayValidationError("replay string is too long")
    if isinstance(value, dict):
        if len(value) > 100_000 or any(not isinstance(key, str) for key in value):
            raise ReplayValidationError("invalid replay object")
        for key, item in value.items():
            _bounded_strings(key)
            _bounded_strings(item)
    elif isinstance(value, list):
        if len(value) > 100_000:
            raise ReplayValidationError("replay array is too long")
        for item in value:
            _bounded_strings(item)


def validate_replay(data: Any) -> Replay:
    if not isinstance(data, dict) or data.get("replay_format_version") != REPLAY_FORMAT_VERSION or data.get("engine_version") != ENGINE_VERSION:
        raise ReplayValidationError("unsupported replay identity")
    _bounded_strings(data)
    try:
        rules = Rules(**data["rules"])
        scenario = Scenario.from_dict(data["scenario"])
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ReplayValidationError(str(error)) from error
    if rules != OFFICIAL_RULES:
        raise ReplayValidationError("replay does not use the official V3 ruleset")
    actions, events, frames, hashes = (data.get(name) for name in ("accepted_actions", "events", "frames", "observation_hashes"))
    if not all(isinstance(item, list) for item in (actions, events, frames, hashes)):
        raise ReplayValidationError("replay sequences are invalid")
    if len(actions) > rules.match_control_ticks or len(frames) > rules.match_control_ticks * rules.physics_steps_per_control + 2:
        raise ReplayValidationError("replay exceeds match bounds")
    if not isinstance(data.get("controllers"), dict) or set(data["controllers"]) != {"A", "B"} or not isinstance(data.get("result"), dict):
        raise ReplayValidationError("invalid replay controllers or result")
    for item in actions:
        if type(item.get("tick")) is not int or not isinstance(item.get("units"), dict):
            raise ReplayValidationError("invalid action record")
        if not 0 <= item["tick"] < rules.match_control_ticks or len(item["units"]) > 16:
            raise ReplayValidationError("action tick/count is out of range")
        for key, action in item["units"].items():
            if not re.fullmatch(r"[AB]:[0-7]", key) or not isinstance(action, dict) or set(action) != {"desired_velocity", "desired_heading", "fire", "reload", "broadcast", "invalid_fields"}:
                raise ReplayValidationError("invalid accepted action")
            vector = action["desired_velocity"]
            if vector is not None and (not isinstance(vector, list) or len(vector) != 2 or any(type(value) not in {int, float} or not math.isfinite(value) for value in vector)):
                raise ReplayValidationError("invalid accepted velocity")
            if action["desired_heading"] is not None and (type(action["desired_heading"]) not in {int, float} or not math.isfinite(action["desired_heading"])):
                raise ReplayValidationError("invalid accepted heading")
            if type(action["fire"]) is not bool or type(action["reload"]) is not bool or type(action["invalid_fields"]) is not int or action["invalid_fields"] < 0:
                raise ReplayValidationError("invalid accepted flags")
            if action["broadcast"] is not None and (type(action["broadcast"]) is not int or not 0 <= action["broadcast"] <= rules.max_radio_payload):
                raise ReplayValidationError("invalid accepted radio payload")
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("units"), list) or len(frame["units"]) != 16:
            raise ReplayValidationError("invalid replay frame")
        if type(frame.get("time")) not in {int, float} or not math.isfinite(frame["time"]) or not 0 <= frame["time"] <= rules.match_seconds:
            raise ReplayValidationError("invalid replay frame time")
        identities = set()
        for unit in frame["units"]:
            if not isinstance(unit, dict) or unit.get("team") not in {"A", "B"} or type(unit.get("unit_id")) is not int or not 0 <= unit["unit_id"] < 8 or type(unit.get("alive")) is not bool:
                raise ReplayValidationError("invalid replay unit identity")
            identities.add((unit["team"], unit["unit_id"]))
            for vector_name in ("position", "velocity", "desired_velocity"):
                vector = unit.get(vector_name)
                if not isinstance(vector, list) or len(vector) != 2 or any(type(value) not in {int, float} or not math.isfinite(value) for value in vector):
                    raise ReplayValidationError("invalid replay unit vector")
            if type(unit.get("health")) is not int or not 0 <= unit["health"] <= rules.max_health or type(unit.get("ammunition")) is not int or not 0 <= unit["ammunition"] <= rules.magazine_size:
                raise ReplayValidationError("invalid replay private state")
            if any(type(unit.get(name)) not in {int, float} or not math.isfinite(unit[name]) for name in ("heading", "desired_heading")):
                raise ReplayValidationError("invalid replay heading")
        if len(identities) != 16:
            raise ReplayValidationError("duplicate replay unit identity")
    if len(hashes) != len(actions):
        raise ReplayValidationError("observation hash count mismatch")
    for values in hashes:
        if not isinstance(values, dict) or any(not re.fullmatch(r"[AB]:[0-7]", key) or not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{24}", value) for key, value in values.items()):
            raise ReplayValidationError("invalid observation hashes")
    allowed_events = {"reload_complete", "reload_start", "radio_transmit", "radio_deliver", "shot", "damage", "death", "controller_failure"}
    for event in events:
        if not isinstance(event, dict) or event.get("type") not in allowed_events or type(event.get("tick")) is not int or not 0 <= event["tick"] <= rules.match_control_ticks:
            raise ReplayValidationError("invalid authoritative event")
        for name in ("origin", "endpoint", "position"):
            if name in event:
                vector = event[name]
                if not isinstance(vector, list) or len(vector) != 2 or any(type(value) not in {int, float} or not math.isfinite(value) for value in vector):
                    raise ReplayValidationError("invalid event position")
        if "payload" in event and (type(event["payload"]) is not int or not 0 <= event["payload"] <= rules.max_radio_payload):
            raise ReplayValidationError("invalid event payload")
    for team in ("A", "B"):
        identity = data["controllers"].get(team)
        if not isinstance(identity, dict) or not isinstance(identity.get("id"), str) or len(identity["id"]) > 200 or not re.fullmatch(r"[0-9a-f]{64}", identity.get("sha256", "")):
            raise ReplayValidationError("invalid controller identity")
    result = data["result"]
    if result.get("winner") not in {None, "A", "B"} or type(result.get("final_tick")) is not int or not 0 <= result["final_tick"] <= rules.match_control_ticks or type(result.get("final_time")) not in {int, float} or not math.isfinite(result["final_time"]) or not 0 <= result["final_time"] <= rules.match_seconds or not re.fullmatch(r"[0-9a-f]{64}", result.get("final_state_hash", "")):
        raise ReplayValidationError("invalid final result")
    return Replay(
        dict(data.get("metadata", {})), rules, scenario,
        {str(key): dict(value) for key, value in data["controllers"].items()},
        tuple(actions), tuple(events), tuple(frames), tuple(hashes), dict(data["result"]),
    )


def save_replay(replay: Replay, path: str | Path) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(replay.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(payload) > MAX_DECOMPRESSED_BYTES:
        raise ReplayValidationError("replay is too large")
    encoded = gzip.compress(payload, compresslevel=6, mtime=0) if destination.suffix == ".gz" else payload
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, destination)
    return destination


def load_replay(path: str | Path) -> Replay:
    source = Path(path)
    if source.stat().st_size > MAX_COMPRESSED_BYTES:
        raise ReplayValidationError("compressed replay is too large")
    raw = source.read_bytes()
    try:
        payload = gzip.decompress(raw) if source.suffix == ".gz" else raw
    except gzip.BadGzipFile as error:
        raise ReplayValidationError("invalid gzip replay") from error
    if len(payload) > MAX_DECOMPRESSED_BYTES:
        raise ReplayValidationError("decompressed replay is too large")
    try:
        return validate_replay(json.loads(payload, parse_constant=lambda value: (_ for _ in ()).throw(ReplayValidationError(f"invalid numeric constant: {value}"))))
    except json.JSONDecodeError as error:
        raise ReplayValidationError("invalid replay JSON") from error


def verify_reconstruction(replay: Replay) -> None:
    simulation = Simulation(replay.scenario, replay.rules)
    for index, record in enumerate(replay.accepted_actions):
        if record["tick"] != simulation.tick:
            raise ReplayValidationError("non-contiguous action ticks")
        observed = simulation.observations()
        expected_hashes = replay.observation_hashes[index]
        actual_hashes = {f"{team.value}:{unit_id}": observation_hash(value) for (team, unit_id), value in observed.items()}
        if expected_hashes != actual_hashes:
            raise ReplayValidationError(f"observation reconstruction mismatch at tick {index}")
        raw = {}
        for key, action in record["units"].items():
            team_name, unit_text = key.split(":", 1)
            raw[(Team(team_name), int(unit_text))] = action
        simulation.step(raw)
        if simulation.result() is not None:
            break
    expected_final = replay.result.get("final_state_hash")
    if expected_final and simulation.canonical_hash() != expected_final:
        raise ReplayValidationError("final authoritative state does not reconstruct")
