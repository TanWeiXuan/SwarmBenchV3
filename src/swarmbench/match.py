"""Sixteen-worker match orchestration with fair barrier failure handling."""

from __future__ import annotations

import hashlib
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .api import Team, UnitInfo
from .arena import generate_scenario
from .controller_runner import ControllerInfrastructureError, ControllerProcess, step_all
from .controllers.baselines import BASELINE_NAMES, baseline_path
from .engine import Simulation, UnitKey
from .replay.format import Replay, observation_hash
from .rules import OFFICIAL_RULES, Rules
from .version import ENGINE_VERSION, RULESET_VERSION


@dataclass(frozen=True, slots=True)
class MatchResult:
    winner: Team | None
    reason: str
    replay: Replay
    stats_a: dict[str, Any]
    stats_b: dict[str, Any]
    wall_time: float

    @property
    def survivors_a(self) -> int:
        return int(self.replay.result["survivors"]["A"])

    @property
    def survivors_b(self) -> int:
        return int(self.replay.result["survivors"]["B"])


def controller_path(value: str | Path) -> Path:
    return baseline_path(str(value)) if str(value) in BASELINE_NAMES else Path(value).resolve()


def controller_identity(path: Path, display_name: str | None = None) -> dict[str, str]:
    return {"id": display_name or path.stem, "path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _controller_seed(master_seed: int, identity: str, team: Team, unit_id: int) -> int:
    payload = f"swarmbench-v3-controller-seed:{master_seed}:{identity}:{team.value}:{unit_id}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _source_revision() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _team_stats(processes: dict[UnitKey, ControllerProcess], team: Team, invalid_actions: int = 0) -> dict[str, Any]:
    summaries = [processes[(team, unit_id)].stats.summary() for unit_id in range(OFFICIAL_RULES.units_per_team)]
    times = [value for process in processes.values() for value in process.stats.step_times]
    return {
        "units": summaries,
        "responses": sum(len(processes[(team, unit_id)].stats.step_times) for unit_id in range(OFFICIAL_RULES.units_per_team)),
        "soft_misses": sum(int(item["soft_misses"]) for item in summaries),
        "hard_failures": sum(int(item["hard_failures"]) for item in summaries),
        "invalid_actions": invalid_actions,
        "max_step": max((float(item["max"]) for item in summaries), default=0.0),
    }


def run_match(
    controller_a: str | Path,
    controller_b: str | Path,
    *,
    seed: int,
    backend: str = "local",
    rules: Rules = OFFICIAL_RULES,
    max_control_ticks: int | None = None,
) -> MatchResult:
    if rules != OFFICIAL_RULES:
        raise ValueError("rated and normal V3 matches use the official ruleset")
    paths = {Team.A: controller_path(controller_a), Team.B: controller_path(controller_b)}
    identities = {team: controller_identity(path, str(controller_a if team is Team.A else controller_b)) for team, path in paths.items()}
    scenario = generate_scenario(seed, rules)
    simulation = Simulation(scenario, rules)
    processes = {(team, unit_id): ControllerProcess(paths[team], backend=backend, rules=rules) for team in (Team.A, Team.B) for unit_id in range(rules.units_per_team)}
    accepted: list[dict[str, Any]] = []
    observation_hashes: list[dict[str, str]] = []
    started = time.perf_counter()
    failed_teams: set[Team] = set()
    failure_details: list[dict[str, Any]] = []
    try:
        infos = {}
        for key, process in processes.items():
            team, unit_id = key
            unit = simulation.units[key]
            infos[key] = UnitInfo(
                rules, (rules.arena_width, rules.arena_height), team, unit_id, rules.units_per_team,
                simulation.self_state(unit), _controller_seed(seed, identities[team]["sha256"], team, unit_id),
            )
        with ThreadPoolExecutor(max_workers=16, thread_name_prefix="swarmbench-unit-init") as executor:
            futures = {executor.submit(process.initialize, infos[key]): key for key, process in processes.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    future.result()
                except BaseException as error:
                    if isinstance(error, ControllerInfrastructureError):
                        raise
                    failed_teams.add(key[0])
                    failure_details.append({"phase": "initialize", "team": key[0].value, "unit_id": key[1], "error": type(error).__name__})
        limit = min(rules.match_control_ticks, max_control_ticks if max_control_ticks is not None else rules.match_control_ticks)
        while not failed_teams and simulation.tick < limit and simulation.result() is None:
            observations = simulation.observations()
            observation_hashes.append({f"{team.value}:{unit_id}": observation_hash(value) for (team, unit_id), value in observations.items()})
            results, errors = step_all(processes, observations)
            infrastructure = next((error for error in errors.values() if isinstance(error, ControllerInfrastructureError)), None)
            if infrastructure is not None:
                raise infrastructure
            for key, error in errors.items():
                failed_teams.add(key[0])
                failure_details.append({"phase": "step", "tick": simulation.tick, "team": key[0].value, "unit_id": key[1], "error": type(error).__name__})
            if failed_teams:
                break
            raw_actions = {key: result.action for key, result in results.items()}
            action_tick = simulation.tick
            validated = simulation.step(raw_actions)
            accepted.append({
                "tick": action_tick,
                "units": {f"{team.value}:{unit_id}": action.to_dict() for (team, unit_id), action in sorted(validated.items(), key=lambda item: (item[0][0].value, item[0][1]))},
            })
        if failed_teams:
            winner = None if len(failed_teams) == 2 else next(iter(failed_teams)).opponent
            reason = "double_controller_forfeit" if len(failed_teams) == 2 else "controller_forfeit"
            simulation.events.extend({"tick": simulation.tick, "type": "controller_failure", **item} for item in failure_details)
        else:
            completed = simulation.result()
            if completed is not None:
                winner, reason = completed
            else:
                alive_a, alive_b = len(simulation.living(Team.A)), len(simulation.living(Team.B))
                winner = Team.A if alive_a > alive_b else Team.B if alive_b > alive_a else None
                reason = "diagnostic_limit_survivors" if winner else "diagnostic_limit_draw"
        survivors = {team.value: len(simulation.living(team)) for team in (Team.A, Team.B)}
        shots = [event for event in simulation.events if event["type"] == "shot"]
        radios = [event for event in simulation.events if event["type"] == "radio_transmit"]
        deliveries = [event for event in simulation.events if event["type"] == "radio_deliver"]
        result_data = {
            "winner": winner.value if winner else None,
            "reason": reason,
            "final_tick": simulation.tick,
            "final_time": simulation.time,
            "survivors": survivors,
            "friendly_fire_hits": sum(bool(event["friendly_fire"]) for event in shots),
            "radio_transmissions": len(radios),
            "radio_deliveries": len(deliveries),
            "final_state_hash": simulation.canonical_hash(),
        }
        replay = Replay(
            {"engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION, "source_revision": _source_revision(), "seed": seed},
            rules, scenario, {team.value: identities[team] for team in (Team.A, Team.B)}, tuple(accepted), tuple(simulation.events),
            tuple(simulation.physics_frames), tuple(observation_hashes), result_data,
        )
        return MatchResult(winner, reason, replay, _team_stats(processes, Team.A, simulation.invalid_actions[Team.A]), _team_stats(processes, Team.B, simulation.invalid_actions[Team.B]), time.perf_counter() - started)
    finally:
        for process in processes.values():
            process.close()
