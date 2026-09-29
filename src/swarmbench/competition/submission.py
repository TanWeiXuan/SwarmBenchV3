"""One-file submission checks and SHA-bound provisional calibration."""

from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from swarmbench.controllers.baselines import BASELINE_NAMES, baseline_path
from swarmbench.match import run_match
from swarmbench.version import ENGINE_VERSION, RULESET_VERSION

from .glicko2 import GlickoRating, update_rating
from .ratings import load_ratings

MAX_SUBMISSION_BYTES = 5 * 1024 * 1024
CALIBRATION_SCHEMA = "swarmbench-calibration-v3"
ALLOWED_IMPORT_ROOTS = set(os.sys.stdlib_module_names) | {"swarmbench"}
SUBMISSION_TEMPLATE = "submissions/example/controller.py"
DEFAULT_CALIBRATION_MATCH_WORKERS = 2


@dataclass(frozen=True, slots=True)
class CalibrationOpponent:
    controller_id: str
    path: Path
    sha256: str
    rating: float
    deviation: float
    volatility: float
    built_in: bool

    def snapshot(self) -> dict[str, Any]:
        return {
            "controller_id": self.controller_id,
            "sha256": self.sha256,
            "rating": self.rating,
            "deviation": self.deviation,
            "volatility": self.volatility,
            "built_in": self.built_in,
        }


def _write_json(data: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate_source(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_SUBMISSION_BYTES:
        raise ValueError("submission must be one regular Python file no larger than 5 MiB")
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (UnicodeDecodeError, SyntaxError) as error:
        raise ValueError(f"invalid Python source: {error}") from error
    imports = set()
    controller_classes = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                root = name.split(".", 1)[0]
                imports.add(root)
                if root not in ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"unavailable import in official worker: {root}")
        if isinstance(node, ast.ClassDef) and node.name == "UnitController":
            controller_classes += 1
    if controller_classes != 1:
        raise ValueError("submission must define exactly one UnitController class")
    return {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "bytes": source.stat().st_size, "imports": sorted(imports)}


def validate_structure(base_ref: str, author: str, root: Path) -> dict[str, Any]:
    completed = subprocess.run(["git", "diff", "--name-status", f"{base_ref}...HEAD"], cwd=root, text=True, capture_output=True, check=True)
    changes = [line.split("\t") for line in completed.stdout.splitlines() if line.strip()]
    if not changes:
        raise ValueError("pull request has no changes")
    submission = [
        parts[-1]
        for parts in changes
        if parts[-1].startswith("submissions/")
        and parts[-1].endswith(".py")
        and parts[-1] != SUBMISSION_TEMPLATE
    ]
    if submission:
        expected_prefix = f"submissions/{author}/"
        if len(changes) != 1 or len(submission) != 1 or changes[0][0] not in {"A", "M"} or not submission[0].startswith(expected_prefix) or not submission[0].endswith(".py"):
            raise ValueError("a submission PR must change exactly submissions/<login>/<controller>.py")
        validate_source(root / submission[0])
        return {"submission_path": submission[0]}
    return {"submission_path": None}


def smoke_test(path: str | Path, *, backend: str, max_control_ticks: int = 30) -> dict[str, Any]:
    match = run_match(path, "rush", seed=0x5300C3, backend=backend, max_control_ticks=max_control_ticks)
    return {
        "schema": "swarmbench-smoke-v3", "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION,
        "controller_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "completed_ticks": match.replay.result["final_tick"],
        "reason": match.reason, "timing": match.stats_a,
    }


def _submission_controller_id(path: str | Path) -> str | None:
    parts = PurePosixPath(str(path).replace("\\", "/")).parts
    indices = [index for index, part in enumerate(parts) if part == "submissions"]
    if indices:
        tail = parts[indices[-1]:]
        if len(tail) == 3 and tail[2].endswith(".py"):
            return f"{tail[1]}/{PurePosixPath(tail[2]).stem}"
    return None


def calibration_opponents(
    submission_path: str | Path,
    ratings_path: str | Path = "leaderboard/ratings.json",
) -> tuple[CalibrationOpponent, ...]:
    ratings_source = Path(ratings_path)
    records = load_ratings(ratings_source)
    repository_root = ratings_source.resolve().parent.parent
    submission_controller_id = _submission_controller_id(submission_path)
    opponents = []

    for controller_id in BASELINE_NAMES:
        record = records.get(controller_id)
        if record is None or not record.built_in:
            raise ValueError(f"missing built-in calibration rating: {controller_id}")
        source = baseline_path(controller_id)
        opponents.append(
            CalibrationOpponent(
                controller_id,
                source,
                hashlib.sha256(source.read_bytes()).hexdigest(),
                record.rating,
                record.deviation,
                record.volatility,
                True,
            )
        )

    community = sorted(
        (record for record in records.values() if not record.built_in and record.controller_id != submission_controller_id),
        key=lambda record: record.controller_id,
    )
    for record in community:
        author, name = record.controller_id.split("/", 1)
        source = repository_root / "submissions" / author / f"{name}.py"
        validation = validate_source(source)
        if validation["sha256"] != record.version_sha:
            raise ValueError(f"community calibration opponent hash mismatch: {record.controller_id}")
        opponents.append(
            CalibrationOpponent(
                record.controller_id,
                source,
                validation["sha256"],
                record.rating,
                record.deviation,
                record.volatility,
                False,
            )
        )
    return tuple(opponents)


def _calibration_game(
    path: str | Path,
    opponent: CalibrationOpponent,
    seed: int,
    side: str,
    backend: str,
) -> dict[str, Any]:
    left, right = (path, opponent.path) if side == "ab" else (opponent.path, path)
    match = run_match(left, right, seed=seed, backend=backend)
    submission_won = match.winner is not None and (
        (side == "ab" and match.winner.value == "A") or (side == "ba" and match.winner.value == "B")
    )
    submission_lost = match.winner is not None and not submission_won
    return {
        "opponent": opponent.controller_id,
        "opponent_sha256": opponent.sha256,
        "seed": seed,
        "side": side,
        "score": 1.0 if submission_won else 0.0 if submission_lost else 0.5,
        "final_state_hash": match.replay.result["final_state_hash"],
        "timing": match.stats_a if side == "ab" else match.stats_b,
    }


def calibration_seed(
    path: str | Path,
    submission_id: str,
    head_sha: str,
    seed_index: int,
    *,
    backend: str,
    match_workers: int = DEFAULT_CALIBRATION_MATCH_WORKERS,
    ratings_path: str | Path = "leaderboard/ratings.json",
) -> dict[str, Any]:
    if not 0 <= seed_index < 4 or not re.fullmatch(r"[0-9a-fA-F]{7,64}", head_sha):
        raise ValueError("invalid calibration identity")
    if match_workers < 1:
        raise ValueError("calibration match worker count must be positive")
    controller_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    opponents = calibration_opponents(path, ratings_path)
    opponent_snapshot = [opponent.snapshot() for opponent in opponents]
    opponent_snapshot_sha256 = hashlib.sha256(json.dumps(opponent_snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    tasks = []
    for opponent in opponents:
        seed_material = f"v3-calibration:{head_sha}:{seed_index}:{opponent.controller_id}:{opponent.sha256}"
        seed = int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big") % 2**63
        for side in ("ab", "ba"):
            tasks.append((path, opponent, seed, side, backend))
    with ThreadPoolExecutor(max_workers=min(match_workers, len(tasks)), thread_name_prefix="calibration-match") as executor:
        futures = [executor.submit(_calibration_game, *task) for task in tasks]
        games = [future.result() for future in futures]
    core = {"schema": CALIBRATION_SCHEMA, "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION, "submission_id": submission_id, "submission_path": str(path), "head_sha": head_sha, "controller_sha256": controller_hash, "seed_index": seed_index, "opponents": opponent_snapshot, "opponent_snapshot_sha256": opponent_snapshot_sha256, "games": games}
    return {**core, "artifact_sha256": hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def _validated_opponent_snapshot(value: Any, submission_path: str) -> list[dict[str, Any]]:
    required = {"controller_id", "sha256", "rating", "deviation", "volatility", "built_in"}
    if not isinstance(value, list) or len(value) < len(BASELINE_NAMES):
        raise ValueError("invalid calibration opponent snapshot")
    controller_ids = []
    for opponent in value:
        if not isinstance(opponent, dict) or set(opponent) != required:
            raise ValueError("invalid calibration opponent snapshot")
        controller_id = opponent["controller_id"]
        numeric = (opponent["rating"], opponent["deviation"], opponent["volatility"])
        if (
            not isinstance(controller_id, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?", controller_id)
            or not isinstance(opponent["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", opponent["sha256"])
            or type(opponent["built_in"]) is not bool
            or any(type(item) not in {int, float} or not math.isfinite(item) for item in numeric)
            or not (0 < opponent["deviation"] <= 350 and 0 < opponent["volatility"] < 2 and -10_000 < opponent["rating"] < 10_000)
        ):
            raise ValueError("invalid calibration opponent snapshot")
        controller_ids.append(controller_id)
    if len(set(controller_ids)) != len(controller_ids):
        raise ValueError("duplicate calibration opponent")
    baseline_count = len(BASELINE_NAMES)
    if controller_ids[:baseline_count] != list(BASELINE_NAMES) or any(not item["built_in"] for item in value[:baseline_count]):
        raise ValueError("invalid built-in calibration opponents")
    community_ids = controller_ids[baseline_count:]
    if community_ids != sorted(community_ids) or any(item["built_in"] for item in value[baseline_count:]):
        raise ValueError("invalid community calibration opponents")
    if _submission_controller_id(submission_path) in controller_ids:
        raise ValueError("submission cannot calibrate against itself")
    return value


def aggregate_calibration(directory: Path, submission_id: str, submission_path: str, head_sha: str) -> dict[str, Any]:
    paths = sorted(directory.rglob("calibration-seed-*.json"))
    if any(path.stat().st_size > 16 * 1024 * 1024 for path in paths):
        raise ValueError("calibration artifact is too large")
    artifacts = [json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid numeric constant: {value}"))) for path in paths]
    if len(artifacts) != 4 or {item.get("seed_index") for item in artifacts} != set(range(4)):
        raise ValueError("all four unique calibration seed artifacts are required")
    games = []
    controller_hash = None
    opponent_snapshot = None
    opponent_snapshot_sha256 = None
    for item in artifacts:
        core = {key: value for key, value in item.items() if key != "artifact_sha256"}
        if item.get("artifact_sha256") != hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
            raise ValueError("calibration artifact integrity mismatch")
        if item.get("schema") != CALIBRATION_SCHEMA or item.get("engine_version") != ENGINE_VERSION or item.get("ruleset_version") != RULESET_VERSION or item.get("submission_id") != submission_id or item.get("submission_path") != submission_path or item.get("head_sha") != head_sha:
            raise ValueError("calibration identity mismatch")
        current_opponents = _validated_opponent_snapshot(item.get("opponents"), submission_path)
        current_snapshot_hash = hashlib.sha256(json.dumps(current_opponents, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if item.get("opponent_snapshot_sha256") != current_snapshot_hash:
            raise ValueError("calibration opponent snapshot integrity mismatch")
        opponent_snapshot = opponent_snapshot or current_opponents
        opponent_snapshot_sha256 = opponent_snapshot_sha256 or current_snapshot_hash
        if current_opponents != opponent_snapshot or current_snapshot_hash != opponent_snapshot_sha256:
            raise ValueError("calibration opponent snapshots disagree")
        controller_hash = controller_hash or item["controller_sha256"]
        expected_games = [
            (opponent["controller_id"], opponent["sha256"], side)
            for opponent in current_opponents
            for side in ("ab", "ba")
        ]
        current_games = item.get("games", [])
        actual_games = [
            (game.get("opponent"), game.get("opponent_sha256"), game.get("side"))
            for game in current_games
            if isinstance(game, dict)
        ]
        if item["controller_sha256"] != controller_hash or actual_games != expected_games or len(current_games) != len(expected_games):
            raise ValueError("calibration controller/count mismatch")
        if any(type(game.get("score")) not in {int, float} or float(game["score"]) not in {0.0, 0.5, 1.0} for game in current_games):
            raise ValueError("invalid calibration score")
        games.extend(current_games)
    assert opponent_snapshot is not None and opponent_snapshot_sha256 is not None
    opponent_ratings = {
        item["controller_id"]: GlickoRating(item["rating"], item["deviation"], item["volatility"])
        for item in opponent_snapshot
    }
    rating = update_rating(GlickoRating(), [(opponent_ratings[game["opponent"]], float(game["score"])) for game in games])
    scores = [float(game["score"]) for game in games]
    unit_stats = [unit for game in games for unit in game["timing"]["units"]]
    result = {
        "schema": CALIBRATION_SCHEMA, "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION,
        "submission_id": submission_id, "submission_path": submission_path, "head_sha": head_sha, "controller_sha256": controller_hash,
        "opponents": opponent_snapshot, "opponent_snapshot_sha256": opponent_snapshot_sha256, "opponent_count": len(opponent_snapshot),
        "match_count": len(games), "wins": scores.count(1.0), "draws": scores.count(0.5), "losses": scores.count(0.0),
        "provisional_rating": rating.rating, "deviation": rating.deviation, "volatility": rating.volatility,
        "timing": {"mean": sum(float(item["mean"]) for item in unit_stats) / max(1, len(unit_stats)), "p95": max((float(item["p95"]) for item in unit_stats), default=0.0), "max": max((float(item["max"]) for item in unit_stats), default=0.0)},
    }
    result["artifact_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result


def validate_controller_cli(path: str | Path, *, backend: str = "local") -> int:
    source = validate_source(path)
    smoke = smoke_test(path, backend=backend)
    print(json.dumps({"source": source, "smoke": smoke, "security": "local backend is not a hostile-code sandbox" if backend == "local" else "docker per-unit isolation"}, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    structure = commands.add_parser("structure"); structure.add_argument("--base-ref", required=True); structure.add_argument("--author", required=True); structure.add_argument("--output", required=True)
    source = commands.add_parser("source"); source.add_argument("path")
    smoke = commands.add_parser("smoke"); smoke.add_argument("path"); smoke.add_argument("--output", required=True)
    calibrate = commands.add_parser("calibrate"); calibrate.add_argument("path"); calibrate.add_argument("--submission-id", required=True); calibrate.add_argument("--head-sha", required=True); calibrate.add_argument("--seed-index", type=int, required=True); calibrate.add_argument("--match-workers", type=int, default=DEFAULT_CALIBRATION_MATCH_WORKERS); calibrate.add_argument("--ratings", type=Path, default=Path("leaderboard/ratings.json")); calibrate.add_argument("--output", required=True)
    aggregate = commands.add_parser("aggregate"); aggregate.add_argument("directory", type=Path); aggregate.add_argument("--submission-id", required=True); aggregate.add_argument("--submission-path", required=True); aggregate.add_argument("--head-sha", required=True); aggregate.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    backend = os.environ.get("SWARMBENCH_BACKEND", "local")
    if arguments.command == "structure": _write_json(validate_structure(arguments.base_ref, arguments.author, Path.cwd()), arguments.output)
    elif arguments.command == "source": validate_source(arguments.path)
    elif arguments.command == "smoke": _write_json(smoke_test(arguments.path, backend=backend), arguments.output)
    elif arguments.command == "calibrate": _write_json(calibration_seed(arguments.path, arguments.submission_id, arguments.head_sha, arguments.seed_index, backend=backend, match_workers=arguments.match_workers, ratings_path=arguments.ratings), arguments.output)
    else: _write_json(aggregate_calibration(arguments.directory, arguments.submission_id, arguments.submission_path, arguments.head_sha), arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
