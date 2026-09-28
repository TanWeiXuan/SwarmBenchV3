"""Frozen, sharded V3 tournaments with fail-closed artifact aggregation."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from swarmbench.controllers.baselines import BASELINE_NAMES, baseline_path
from swarmbench.match import run_match
from swarmbench.replay import save_replay
from swarmbench.version import ENGINE_VERSION, RULESET_VERSION, TOURNAMENT_FORMAT_VERSION

from .matchmaking import MatchmakingEntry, ScheduledGame, schedule_games, select_pairings
from .ratings import RatingRecord, apply_rating_period, load_ratings, save_ratings

MAX_TOURNAMENT_BATCHES = 19
SIZE_PRESETS = {"small": (2, 1), "default": (8, 4), "large": (12, 8)}
CONTROLLER_ID = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _source_revision(root: Path) -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def resolve_controller_paths(records: dict[str, RatingRecord], root: Path) -> dict[str, Path]:
    paths = {}
    for controller_id, record in records.items():
        if not CONTROLLER_ID.fullmatch(controller_id):
            raise ValueError("invalid controller ID")
        if record.built_in:
            paths[controller_id] = baseline_path(controller_id)
        else:
            login, name = controller_id.split("/", 1)
            paths[controller_id] = root / "submissions" / login / f"{name}.py"
        if not paths[controller_id].is_file():
            raise ValueError(f"controller file is missing: {controller_id}")
    return paths


@dataclass(frozen=True, slots=True)
class TournamentPlan:
    seed: int
    mode: str
    size: str
    source_revision: str
    controller_hashes: dict[str, str]
    pairings: tuple[tuple[str, str], ...]
    games: tuple[ScheduledGame, ...]
    batches: tuple[tuple[str, ...], ...]

    def to_dict(self) -> dict[str, Any]:
        core = {
            "format_version": TOURNAMENT_FORMAT_VERSION,
            "engine_version": ENGINE_VERSION,
            "ruleset_version": RULESET_VERSION,
            "seed": self.seed,
            "mode": self.mode,
            "size": self.size,
            "source_revision": self.source_revision,
            "controller_hashes": self.controller_hashes,
            "pairings": [list(pair) for pair in self.pairings],
            "games": [asdict(game) for game in self.games],
            "batches": [list(batch) for batch in self.batches],
        }
        return {**core, "plan_sha256": _canonical_hash(core)}


def plan_from_dict(data: Any) -> TournamentPlan:
    if not isinstance(data, dict):
        raise ValueError("plan must be an object")
    _validate_primitive(data)
    supplied = data.get("plan_sha256")
    core = {key: value for key, value in data.items() if key != "plan_sha256"}
    if supplied != _canonical_hash(core):
        raise ValueError("plan integrity mismatch")
    if data.get("format_version") != TOURNAMENT_FORMAT_VERSION or data.get("engine_version") != ENGINE_VERSION or data.get("ruleset_version") != RULESET_VERSION:
        raise ValueError("plan version mismatch")
    if data.get("mode") not in {"official", "exhibition"} or data.get("size") not in SIZE_PRESETS:
        raise ValueError("invalid plan mode or size")
    if type(data.get("seed")) is not int or not 0 <= data["seed"] < 2**63:
        raise ValueError("invalid tournament seed")
    if not isinstance(data.get("controller_hashes"), dict) or not data["controller_hashes"] or any(not CONTROLLER_ID.fullmatch(key) or not re.fullmatch(r"[0-9a-f]{64}", value) for key, value in data["controller_hashes"].items()):
        raise ValueError("invalid frozen controller hashes")
    games = tuple(ScheduledGame(**item) for item in data["games"])
    plan = TournamentPlan(int(data["seed"]), data["mode"], data["size"], str(data["source_revision"]), dict(data["controller_hashes"]), tuple(tuple(pair) for pair in data["pairings"]), games, tuple(tuple(batch) for batch in data["batches"]))
    if len(plan.batches) > MAX_TOURNAMENT_BATCHES or len({game.game_id for game in games}) != len(games) or sorted(game.game_id for game in games) != sorted(game_id for batch in plan.batches for game_id in batch) or any(game.controller_a not in plan.controller_hashes or game.controller_b not in plan.controller_hashes or type(game.scenario_seed) is not int for game in games):
        raise ValueError("invalid frozen batch schedule")
    return plan


def create_plan(records: dict[str, RatingRecord], seed: int, *, mode: str, size: str, root: Path | None = None) -> TournamentPlan:
    if mode not in {"official", "exhibition"} or size not in SIZE_PRESETS:
        raise ValueError("invalid tournament mode or size")
    root = (root or Path.cwd()).resolve()
    paths = resolve_controller_paths(records, root)
    controller_hashes = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items()}
    target_opponents, scenario_count = SIZE_PRESETS[size]
    pairings = select_pairings([MatchmakingEntry(key, record.rating) for key, record in sorted(records.items())], seed, target_opponents)
    games = schedule_games(pairings, seed, scenario_count)
    batch_count = min(MAX_TOURNAMENT_BATCHES, max(1, len(games)))
    batches = tuple(tuple(game.game_id for game in games[index::batch_count]) for index in range(batch_count))
    return TournamentPlan(seed, mode, size, _source_revision(root), controller_hashes, pairings, games, batches)


def _batch_hash(batch: dict[str, Any]) -> str:
    return _canonical_hash({key: value for key, value in batch.items() if key != "artifact_sha256"})


def _validate_primitive(value: Any, *, depth: int = 0) -> None:
    if depth > 12:
        raise ValueError("artifact nesting is too deep")
    if value is None or type(value) in {bool, int, str}:
        if isinstance(value, str) and len(value) > 4096:
            raise ValueError("artifact string is too long")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("artifact number is not finite")
        return
    if isinstance(value, list):
        if len(value) > 100_000:
            raise ValueError("artifact list is too long")
        for item in value:
            _validate_primitive(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100_000 or any(not isinstance(key, str) or len(key) > 256 for key in value):
            raise ValueError("invalid artifact object")
        for item in value.values():
            _validate_primitive(item, depth=depth + 1)
        return
    raise ValueError("artifact contains a non-primitive value")


def execute_batch(plan: TournamentPlan, batch_index: int, controller_paths: dict[str, Path], *, backend: str = "local", replay_dir: Path | None = None, max_control_ticks: int | None = None) -> dict[str, Any]:
    if not 0 <= batch_index < len(plan.batches):
        raise ValueError("invalid batch index")
    for controller_id, expected_hash in plan.controller_hashes.items():
        if hashlib.sha256(controller_paths[controller_id].read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"controller hash changed after schedule freeze: {controller_id}")
    expected = set(plan.batches[batch_index])
    results = []
    candidate: tuple[tuple[int, str], Any, str] | None = None
    for game in plan.games:
        if game.game_id not in expected:
            continue
        match = run_match(controller_paths[game.controller_a], controller_paths[game.controller_b], seed=game.scenario_seed, backend=backend, max_control_ticks=max_control_ticks)
        score = 0.5 if match.winner is None else 1.0 if match.winner.value == "A" else 0.0
        result = {
            "game_id": game.game_id, "pairing_id": game.pairing_id,
            "controller_a": game.controller_a, "controller_b": game.controller_b,
            "controller_hash_a": plan.controller_hashes[game.controller_a], "controller_hash_b": plan.controller_hashes[game.controller_b],
            "scenario_seed": game.scenario_seed, "result_a": score,
            "survivors_a": match.survivors_a, "survivors_b": match.survivors_b,
            "reason": match.reason, "final_state_hash": match.replay.result["final_state_hash"],
            "stats_a": match.stats_a, "stats_b": match.stats_b,
            "diagnostics": {"wall_time": match.wall_time, "friendly_fire_hits": match.replay.result["friendly_fire_hits"], "radio_transmissions": match.replay.result["radio_transmissions"], "radio_deliveries": match.replay.result["radio_deliveries"]},
        }
        results.append(result)
        rank = (abs(match.survivors_a - match.survivors_b), game.game_id)
        if candidate is None or rank < candidate[0]:
            candidate = (rank, match.replay, game.game_id)
    replay_artifacts = []
    if replay_dir is not None and candidate is not None:
        replay_dir.mkdir(parents=True, exist_ok=True)
        destination = save_replay(candidate[1], replay_dir / f"candidate-{candidate[2]}.json.gz")
        replay_artifacts.append({"game_id": candidate[2], "name": destination.name, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
    batch = {
        "format_version": TOURNAMENT_FORMAT_VERSION, "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION,
        "plan_sha256": plan.to_dict()["plan_sha256"], "tournament_seed": plan.seed, "source_revision": plan.source_revision,
        "batch_index": batch_index, "expected_game_ids": sorted(expected), "games": results, "replay_artifacts": replay_artifacts,
    }
    batch["artifact_sha256"] = _batch_hash(batch)
    return batch


def validate_batch(plan: TournamentPlan, batch: Any, batch_index: int) -> list[dict[str, Any]]:
    if not isinstance(batch, dict) or batch.get("artifact_sha256") != _batch_hash(batch):
        raise ValueError("batch integrity mismatch")
    _validate_primitive(batch)
    if batch.get("format_version") != TOURNAMENT_FORMAT_VERSION or batch.get("engine_version") != ENGINE_VERSION or batch.get("ruleset_version") != RULESET_VERSION or batch.get("plan_sha256") != plan.to_dict()["plan_sha256"] or type(batch.get("tournament_seed")) is not int or batch.get("tournament_seed") != plan.seed or batch.get("source_revision") != plan.source_revision or type(batch.get("batch_index")) is not int or batch.get("batch_index") != batch_index:
        raise ValueError("batch identity mismatch")
    expected = set(plan.batches[batch_index])
    if batch.get("expected_game_ids") != sorted(expected) or not isinstance(batch.get("games"), list) or len(batch["games"]) != len(expected):
        raise ValueError("batch schedule mismatch")
    scheduled = {game.game_id: game for game in plan.games}
    seen = set()
    for result in batch["games"]:
        if not isinstance(result, dict):
            raise ValueError("game result must be an object")
        game_id = result.get("game_id")
        if game_id in seen or game_id not in expected:
            raise ValueError("duplicate or unexpected game")
        game = scheduled[game_id]
        if result.get("controller_a") != game.controller_a or result.get("controller_b") != game.controller_b or result.get("controller_hash_a") != plan.controller_hashes[game.controller_a] or result.get("controller_hash_b") != plan.controller_hashes[game.controller_b] or type(result.get("scenario_seed")) is not int or result.get("scenario_seed") != game.scenario_seed or type(result.get("result_a")) is not float or result.get("result_a") not in {0.0, 0.5, 1.0}:
            raise ValueError("game does not match frozen schedule")
        if any(type(result.get(name)) is not int or not 0 <= result[name] <= 8 for name in ("survivors_a", "survivors_b")):
            raise ValueError("invalid survivor count")
        if not isinstance(result.get("final_state_hash"), str) or len(result["final_state_hash"]) != 64:
            raise ValueError("invalid final state hash")
        seen.add(game_id)
    artifacts = batch.get("replay_artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > 1:
        raise ValueError("invalid replay artifact list")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"game_id", "name", "sha256"} or artifact["game_id"] not in expected or artifact["name"] != f"candidate-{artifact['game_id']}.json.gz" or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]):
            raise ValueError("invalid replay artifact identity")
    if seen != expected:
        raise ValueError("batch is incomplete")
    return batch["games"]


@dataclass(frozen=True, slots=True)
class TournamentOutcome:
    games: tuple[dict[str, Any], ...]
    ratings_before: dict[str, RatingRecord]
    ratings_after: dict[str, RatingRecord]


def aggregate_batches(plan: TournamentPlan, batches: list[dict[str, Any]], ratings: dict[str, RatingRecord]) -> TournamentOutcome:
    if len(batches) != len(plan.batches):
        raise ValueError("all planned batches are required exactly once")
    games = tuple(result for index, batch in enumerate(batches) for result in validate_batch(plan, batch, index))
    if len(games) != len(plan.games) or len({game["game_id"] for game in games}) != len(games):
        raise ValueError("tournament result set is incomplete")
    if set(ratings) != set(plan.controller_hashes):
        raise ValueError("rating participants changed after schedule freeze")
    observations = [(game["controller_a"], game["controller_b"], float(game["result_a"])) for game in games]
    after = apply_rating_period(ratings, observations) if plan.mode == "official" else dict(ratings)
    return TournamentOutcome(games, dict(ratings), after)


def tournament_cli(*, seed: int, size: str, mode: str, backend: str = "local", max_control_ticks: int | None = None) -> int:
    root = Path.cwd()
    ratings_path = root / "leaderboard" / "ratings.json"
    ratings = load_ratings(ratings_path)
    plan = create_plan(ratings, seed, mode=mode, size=size, root=root)
    paths = resolve_controller_paths(ratings, root)
    batches = [execute_batch(plan, index, paths, backend=backend, max_control_ticks=max_control_ticks) for index in range(len(plan.batches))]
    outcome = aggregate_batches(plan, batches, ratings)
    if mode == "official":
        save_ratings(outcome.ratings_after, ratings_path)
    print(f"{mode} tournament: {len(plan.pairings)} pairings, {len(outcome.games)} side-swapped games")
    return 0
