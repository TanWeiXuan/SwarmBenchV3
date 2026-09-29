"""Workflow entry points for frozen compute, fail-closed finalization and Discussions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from .publisher import update_leaderboard
from .ratings import RatingRecord, load_ratings, ratings_to_dict, save_ratings
from .reporting import cell, final_summary, initial_discussion_body, progress_summary, utc_now
from .tournament import _batch_hash, aggregate_batches, create_plan, execute_batch, plan_from_dict, resolve_controller_paths, validate_batch

PLAN_SCHEMA = "swarmbench-tournament-plan-v3"
MAX_JSON_ARTIFACT_BYTES = 16 * 1024 * 1024


def resolve_seed(value: str | None, fallback: str) -> int:
    source = value if value not in {None, ""} else fallback
    try:
        return int(source)
    except ValueError:
        return int.from_bytes(hashlib.sha256(source.encode()).digest()[:8], "big") % 2**63


def _atomic_json(data: Any, path: Path) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _load_json(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > MAX_JSON_ARTIFACT_BYTES:
        raise ValueError(f"JSON artifact is missing or too large: {path}")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid numeric constant: {value}")))


def prepare_plan(ratings_path: Path, *, seed: int, mode: str, size: str, run_id: str, repository: str, root: Path) -> dict[str, Any]:
    ratings = load_ratings(ratings_path)
    plan = create_plan(ratings, seed, mode=mode, size=size, root=root)
    data = plan.to_dict()
    data.update({"schema": PLAN_SCHEMA, "run_id": str(run_id), "repository": repository, "started_at": utc_now(), "ratings_sha256": hashlib.sha256(ratings_path.read_bytes()).hexdigest(), "rating_snapshot": ratings_to_dict(ratings)})
    identity = {key: value for key, value in data.items() if key != "context_sha256"}
    data["context_sha256"] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return data


def validate_context(data: Any) -> tuple[Any, dict[str, Any]]:
    if not isinstance(data, dict) or data.get("schema") != PLAN_SCHEMA:
        raise ValueError("invalid tournament context schema")
    identity = {key: value for key, value in data.items() if key != "context_sha256"}
    if data.get("context_sha256") != hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
        raise ValueError("tournament context integrity mismatch")
    plan_keys = {"format_version", "engine_version", "ruleset_version", "seed", "mode", "size", "source_revision", "controller_hashes", "pairings", "games", "batches", "plan_sha256"}
    plan = plan_from_dict({key: data[key] for key in plan_keys})
    return plan, data


def _discussion_graphql(query: str, **variables: Any) -> dict[str, Any]:
    command = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        command.extend(["-F" if isinstance(value, int) else "-f", f"{key}={value}"])
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return json.loads(completed.stdout)["data"]


def create_discussion(context: dict[str, Any]) -> dict[str, str]:
    owner, name = context["repository"].split("/", 1)
    query = "query($owner:String!,$name:String!){repository(owner:$owner,name:$name){id discussionCategories(first:50){nodes{id name slug}}}}"
    repository = _discussion_graphql(query, owner=owner, name=name)["repository"]
    category = next((item for item in repository["discussionCategories"]["nodes"] if item["slug"] == "tournament-results" or item["name"] == "Tournament Results"), None)
    if category is None:
        raise RuntimeError("Tournament Results Discussion category is not configured")
    title = f"Tournament {context['run_id']} — {context['mode']} / {context['size']}"
    body = initial_discussion_body(context)
    mutation = "mutation($repositoryId:ID!,$categoryId:ID!,$title:String!,$body:String!){createDiscussion(input:{repositoryId:$repositoryId,categoryId:$categoryId,title:$title,body:$body}){discussion{id url}}}"
    return _discussion_graphql(mutation, repositoryId=repository["id"], categoryId=category["id"], title=title, body=body)["createDiscussion"]["discussion"]


def add_discussion_comment(discussion_id: str, body: str) -> None:
    mutation = "mutation($id:ID!,$body:String!){addDiscussionComment(input:{discussionId:$id,body:$body}){comment{id}}}"
    _discussion_graphql(mutation, id=discussion_id, body=body)


def reconcile_current_ratings(current: dict[str, RatingRecord], snapshot: dict[str, RatingRecord], updated: dict[str, RatingRecord]) -> dict[str, RatingRecord]:
    """Preserve controllers accepted after planning; reject changes to frozen participants."""
    if any(current.get(key) != record for key, record in snapshot.items()):
        raise ValueError("a frozen participant changed or disappeared before publication")
    merged = dict(current)
    merged.update(updated)
    return merged


def _gh_json(arguments: list[str]) -> Any:
    completed = subprocess.run(["gh", *arguments], text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip())
    return json.loads(completed.stdout)


def _download_batch(context: dict[str, Any], artifact: dict[str, Any], index: int) -> Any:
    """Read only the expected JSON member; never extract or execute archive content."""
    if artifact.get('expired') or artifact.get('size_in_bytes', 0) > 128 * 1024 * 1024:
        raise ValueError('batch archive expired or too large')
    with tempfile.TemporaryFile() as archive:
        subprocess.run(['gh', 'api', f"repos/{context['repository']}/actions/artifacts/{int(artifact['id'])}/zip"], stdout=archive, check=True, timeout=120)
        if archive.tell() > 128 * 1024 * 1024:
            raise ValueError('batch archive too large')
        archive.seek(0)
        with zipfile.ZipFile(archive) as zipped:
            members = [item for item in zipped.infolist() if item.filename == f'batch-{index}.json']
            if len(members) != 1 or members[0].file_size > MAX_JSON_ARTIFACT_BYTES:
                raise ValueError('missing, duplicate or oversized batch JSON')
            return json.loads(zipped.read(members[0]), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'invalid numeric constant: {value}')))


def _validated_progress_batch(plan: Any, context: dict[str, Any], batch: Any, index: int) -> list[dict[str, Any]]:
    if not isinstance(batch, dict) or any(batch.get(key) != context[key] for key in ('run_id', 'repository', 'context_sha256')):
        raise ValueError('batch run/repository context mismatch')
    return validate_batch(plan, batch, index)


def live_report(context: dict[str, Any], *, run_id: str, output: Path, timeout_seconds: int = 10_800) -> None:
    plan, context = validate_context(context)
    discussion = create_discussion(context)
    _atomic_json(discussion, output)
    expected = len(context["batches"])
    completed: dict[int, list[dict[str, Any]]] = {}
    reported = 0
    deadline = time.monotonic() + timeout_seconds
    milestones = [20, 40, 60, 80, 100]
    while reported < 100:
        artifacts = _gh_json(["api", f"repos/{context['repository']}/actions/runs/{run_id}/artifacts?per_page=100"])
        jobs = _gh_json(["api", f"repos/{context['repository']}/actions/runs/{run_id}/jobs?per_page=100"])
        successful = {job['name'] for job in jobs.get('jobs', []) if job.get('conclusion') == 'success'}
        by_name = {item['name']: item for item in artifacts.get('artifacts', [])}
        try:
            for index in range(expected):
                name = f'tournament-batch-{index}'
                if index not in completed and name in by_name and f'Compute batch {index + 1} (untrusted controllers)' in successful:
                    completed[index] = _validated_progress_batch(plan, context, _download_batch(context, by_name[name], index), index)
        except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            add_discussion_comment(discussion['id'], f'## Status: FAILED\n\nProgress artifact validation failed: {cell(exc)}. No rating update was authorized. See the Actions run for details.')
            raise
        ready = len(completed)
        games = [game for index in sorted(completed) for game in completed[index]]
        percent = len(games) * 100 // len(plan.games) if plan.games else 100
        reached = [milestone for milestone in milestones if reported < milestone <= percent]
        if reached:
            add_discussion_comment(discussion['id'], progress_summary(context, games, ready))
            reported = max(reached)
        if ready >= expected:
            break
        failed_compute = next((job for job in jobs.get("jobs", []) if job.get("name", "").startswith("Compute batch") and job.get("conclusion") not in {None, "success"}), None)
        if failed_compute is not None:
            add_discussion_comment(discussion["id"], f"Status: FAILED\n\nCompute job `{failed_compute['name']}` concluded `{failed_compute['conclusion']}`.")
            raise RuntimeError("tournament compute failed")
        run = _gh_json(["api", f"repos/{context['repository']}/actions/runs/{run_id}"])
        if run.get("status") == "completed" and run.get("conclusion") not in {None, "success"}:
            add_discussion_comment(discussion["id"], f"Status: FAILED\n\nWorkflow concluded `{run.get('conclusion')}` before every compute artifact arrived.")
            raise RuntimeError("tournament workflow failed during compute")
        if time.monotonic() >= deadline:
            add_discussion_comment(discussion["id"], "Status: FAILED\n\nTimed out waiting for compute artifacts.")
            raise TimeoutError("timed out waiting for tournament batches")
        time.sleep(15)
    _atomic_json(discussion, output)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare"); prepare.add_argument("--ratings", type=Path, required=True); prepare.add_argument("--mode", required=True); prepare.add_argument("--size", required=True); prepare.add_argument("--seed"); prepare.add_argument("--run-id", required=True); prepare.add_argument("--repository", required=True); prepare.add_argument("--output", type=Path, required=True)
    compute = commands.add_parser("compute"); compute.add_argument("--plan", type=Path, required=True); compute.add_argument("--batch", type=int, required=True); compute.add_argument("--output", type=Path, required=True); compute.add_argument("--replay-dir", type=Path)
    final = commands.add_parser("final"); final.add_argument("--plan", type=Path, required=True); final.add_argument("--batches", type=Path, required=True); final.add_argument("--ratings", type=Path, required=True); final.add_argument("--readme", type=Path, required=True); final.add_argument("--output", type=Path, required=True)
    discussion = commands.add_parser("discussion"); discussion.add_argument("--plan", type=Path, required=True); discussion.add_argument("--output", type=Path, required=True)
    live = commands.add_parser("live-report"); live.add_argument("--plan", type=Path, required=True); live.add_argument("--run-id", required=True); live.add_argument("--output", type=Path, required=True)
    comment = commands.add_parser("comment"); comment.add_argument("--discussion-id", required=True); comment.add_argument("--result", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "prepare":
        seed = resolve_seed(arguments.seed, arguments.run_id)
        _atomic_json(prepare_plan(arguments.ratings, seed=seed, mode=arguments.mode, size=arguments.size, run_id=arguments.run_id, repository=arguments.repository, root=Path.cwd()), arguments.output)
    elif arguments.command == "compute":
        plan, context = validate_context(_load_json(arguments.plan))
        records = load_ratings(Path("leaderboard/ratings.json"))
        paths = resolve_controller_paths(records, Path.cwd())
        diagnostic_ticks = os.environ.get("SWARMBENCH_MAX_CONTROL_TICKS")
        batch = execute_batch(plan, arguments.batch, paths, backend=os.environ.get("SWARMBENCH_BACKEND", "local"), replay_dir=arguments.replay_dir, max_control_ticks=int(diagnostic_ticks) if diagnostic_ticks else None)
        batch.update({"run_id": context["run_id"], "repository": context["repository"], "context_sha256": context["context_sha256"]})
        batch["artifact_sha256"] = _batch_hash(batch)
        _atomic_json(batch, arguments.output)
    elif arguments.command == "final":
        plan, context = validate_context(_load_json(arguments.plan))
        batches = [_load_json(path) for path in arguments.batches.rglob("batch-*.json")]
        if any(type(batch.get("batch_index")) is not int for batch in batches):
            raise ValueError("invalid batch index")
        batches.sort(key=lambda item: item["batch_index"])
        if any(batch.get("run_id") != context["run_id"] or batch.get("repository") != context["repository"] or batch.get("context_sha256") != context["context_sha256"] for batch in batches):
            raise ValueError("batch run/repository context mismatch")
        current = load_ratings(arguments.ratings)
        snapshot = {item["controller_id"]: RatingRecord(**item) for item in context["rating_snapshot"]["controllers"]}
        if context["rating_snapshot"].get("schema_version") != 3:
            raise ValueError("invalid frozen rating schema")
        outcome = aggregate_batches(plan, batches, snapshot)
        if plan.mode == "official":
            merged = reconcile_current_ratings(current, snapshot, outcome.ratings_after)
            save_ratings(merged, arguments.ratings)
            update_leaderboard(arguments.readme, merged)
        _atomic_json({"schema": "swarmbench-tournament-result-v3", "run_id": context["run_id"], "repository": context["repository"], "mode": plan.mode, "plan_sha256": plan.to_dict()["plan_sha256"], "game_count": len(outcome.games), "discussion_body": final_summary(outcome, plan.mode, context)}, arguments.output)
    elif arguments.command == "discussion":
        _, context = validate_context(_load_json(arguments.plan))
        _atomic_json(create_discussion(context), arguments.output)
    elif arguments.command == "live-report":
        _, context = validate_context(_load_json(arguments.plan))
        live_report(context, run_id=arguments.run_id, output=arguments.output)
    else:
        result = _load_json(arguments.result)
        add_discussion_comment(arguments.discussion_id, result["discussion_body"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
