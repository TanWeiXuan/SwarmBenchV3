"""One-file submission checks and SHA-bound provisional calibration."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from swarmbench.controllers.baselines import BASELINE_NAMES, baseline_path
from swarmbench.match import run_match
from swarmbench.version import ENGINE_VERSION, RULESET_VERSION

from .glicko2 import GlickoRating, update_rating

MAX_SUBMISSION_BYTES = 5 * 1024 * 1024
CALIBRATION_SCHEMA = "swarmbench-calibration-v3"
ALLOWED_IMPORT_ROOTS = set(os.sys.stdlib_module_names) | {"swarmbench"}


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
    submission = [parts[-1] for parts in changes if parts[-1].startswith("submissions/")]
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


def calibration_seed(path: str | Path, submission_id: str, head_sha: str, seed_index: int, *, backend: str) -> dict[str, Any]:
    if not 0 <= seed_index < 4 or not re.fullmatch(r"[0-9a-fA-F]{7,64}", head_sha):
        raise ValueError("invalid calibration identity")
    controller_hash = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    games = []
    for opponent_index, opponent in enumerate(BASELINE_NAMES):
        seed = int.from_bytes(hashlib.sha256(f"v3-calibration:{head_sha}:{seed_index}:{opponent_index}".encode()).digest()[:8], "big") % 2**63
        for side in ("ab", "ba"):
            left, right = (path, baseline_path(opponent)) if side == "ab" else (baseline_path(opponent), path)
            match = run_match(left, right, seed=seed, backend=backend)
            submission_won = match.winner is not None and ((side == "ab" and match.winner.value == "A") or (side == "ba" and match.winner.value == "B"))
            submission_lost = match.winner is not None and not submission_won
            games.append({
                "opponent": opponent, "seed": seed, "side": side,
                "score": 1.0 if submission_won else 0.0 if submission_lost else 0.5,
                "final_state_hash": match.replay.result["final_state_hash"], "timing": match.stats_a if side == "ab" else match.stats_b,
            })
    core = {"schema": CALIBRATION_SCHEMA, "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION, "submission_id": submission_id, "submission_path": str(path), "head_sha": head_sha, "controller_sha256": controller_hash, "seed_index": seed_index, "games": games}
    return {**core, "artifact_sha256": hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}


def aggregate_calibration(directory: Path, submission_id: str, submission_path: str, head_sha: str) -> dict[str, Any]:
    paths = sorted(directory.rglob("calibration-seed-*.json"))
    if any(path.stat().st_size > 16 * 1024 * 1024 for path in paths):
        raise ValueError("calibration artifact is too large")
    artifacts = [json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid numeric constant: {value}"))) for path in paths]
    if len(artifacts) != 4 or {item.get("seed_index") for item in artifacts} != set(range(4)):
        raise ValueError("all four unique calibration seed artifacts are required")
    games = []
    controller_hash = None
    for item in artifacts:
        core = {key: value for key, value in item.items() if key != "artifact_sha256"}
        if item.get("artifact_sha256") != hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
            raise ValueError("calibration artifact integrity mismatch")
        if item.get("schema") != CALIBRATION_SCHEMA or item.get("engine_version") != ENGINE_VERSION or item.get("ruleset_version") != RULESET_VERSION or item.get("submission_id") != submission_id or item.get("submission_path") != submission_path or item.get("head_sha") != head_sha:
            raise ValueError("calibration identity mismatch")
        controller_hash = controller_hash or item["controller_sha256"]
        if item["controller_sha256"] != controller_hash or len(item.get("games", [])) != len(BASELINE_NAMES) * 2:
            raise ValueError("calibration controller/count mismatch")
        games.extend(item["games"])
    rating = update_rating(GlickoRating(), [(GlickoRating(), float(game["score"])) for game in games])
    scores = [float(game["score"]) for game in games]
    unit_stats = [unit for game in games for unit in game["timing"]["units"]]
    result = {
        "schema": CALIBRATION_SCHEMA, "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION,
        "submission_id": submission_id, "submission_path": submission_path, "head_sha": head_sha, "controller_sha256": controller_hash,
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
    calibrate = commands.add_parser("calibrate"); calibrate.add_argument("path"); calibrate.add_argument("--submission-id", required=True); calibrate.add_argument("--head-sha", required=True); calibrate.add_argument("--seed-index", type=int, required=True); calibrate.add_argument("--output", required=True)
    aggregate = commands.add_parser("aggregate"); aggregate.add_argument("directory", type=Path); aggregate.add_argument("--submission-id", required=True); aggregate.add_argument("--submission-path", required=True); aggregate.add_argument("--head-sha", required=True); aggregate.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    backend = os.environ.get("SWARMBENCH_BACKEND", "local")
    if arguments.command == "structure": _write_json(validate_structure(arguments.base_ref, arguments.author, Path.cwd()), arguments.output)
    elif arguments.command == "source": validate_source(arguments.path)
    elif arguments.command == "smoke": _write_json(smoke_test(arguments.path, backend=backend), arguments.output)
    elif arguments.command == "calibrate": _write_json(calibration_seed(arguments.path, arguments.submission_id, arguments.head_sha, arguments.seed_index, backend=backend), arguments.output)
    else: _write_json(aggregate_calibration(arguments.directory, arguments.submission_id, arguments.submission_path, arguments.head_sha), arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
