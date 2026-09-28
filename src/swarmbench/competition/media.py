"""Deterministic at-most-three tournament media selection and publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from swarmbench.replay import load_replay
from swarmbench.replay.renderer import render_replay

from .tournament import _batch_hash


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_media(replay_root: Path, output: Path, *, run_id: str, width: int = 1200) -> dict[str, Any]:
    expected: dict[str, tuple[str, str]] = {}
    for batch_path in replay_root.rglob("batch-*.json"):
        batch = json.loads(batch_path.read_text(encoding="utf-8"))
        if batch.get("artifact_sha256") != _batch_hash(batch):
            raise ValueError(f"batch integrity mismatch: {batch_path.name}")
        for item in batch.get("replay_artifacts", []):
            if item["name"] in expected:
                raise ValueError("duplicate replay artifact name")
            expected[item["name"]] = (item["game_id"], item["sha256"])
    candidates = []
    for path in replay_root.rglob("candidate-*.json.gz"):
        if expected:
            identity = expected.get(path.name)
            if identity is None or identity[1] != file_hash(path):
                raise ValueError(f"replay artifact integrity mismatch: {path.name}")
        replay = load_replay(path)
        game_id = path.name.removeprefix("candidate-").removesuffix(".json.gz")
        if expected and expected[path.name][0] != game_id:
            raise ValueError("replay game identity mismatch")
        difference = abs(int(replay.result["survivors"]["A"]) - int(replay.result["survivors"]["B"]))
        candidates.append((difference, game_id, path, replay))
    selected = sorted(candidates, key=lambda item: (item[0], item[1]))[:3]
    output.mkdir(parents=True, exist_ok=True)
    assets = []
    for _, game_id, replay_path, replay in selected:
        prefix = f"run-{run_id}-{game_id}"
        copied = output / f"{prefix}.json.gz"
        copied.write_bytes(replay_path.read_bytes())
        video = render_replay(replay, output / f"{prefix}.mp4")
        poster = video.with_suffix(".png")
        assets.append({"game_id": game_id, "replay": copied.name, "replay_sha256": file_hash(copied), "video": video.name, "video_sha256": file_hash(video), "poster": poster.name, "poster_sha256": file_hash(poster), "final_state_hash": replay.result["final_state_hash"]})
    manifest = {"schema": "swarmbench-media-manifest-v3", "run_id": run_id, "selection": "smallest survivor difference, then game ID; at most three", "assets": assets}
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    (output / "media-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def immutable_releases_enabled(repository: str) -> bool | None:
    """Return the policy when readable, or None when the token cannot inspect it."""
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/immutable-releases"],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        return None
    try:
        return bool(json.loads(result.stdout).get("enabled", False))
    except (AttributeError, json.JSONDecodeError):
        return None


def publish_media(directory: Path, repository: str, run_id: str) -> list[str]:
    tag = f"tournament-media-run-{run_id}"
    title = f"Tournament media run {run_id}"
    view = subprocess.run(["gh", "release", "view", tag, "--repo", repository], capture_output=True)
    if view.returncode:
        created = subprocess.run(["gh", "release", "create", tag, "--repo", repository, "--title", title, "--notes", "Durable SwarmBenchV3 tournament media. This is not a software release.", "--latest=false"], capture_output=True, text=True)
        if created.returncode and subprocess.run(["gh", "release", "view", tag, "--repo", repository], capture_output=True).returncode:
            raise RuntimeError(created.stderr.strip())
    files = sorted(path for path in directory.iterdir() if path.suffix in {".mp4", ".png", ".gz", ".json"})
    data = json.loads(subprocess.run(["gh", "api", f"repos/{repository}/releases/tags/{tag}"], text=True, capture_output=True, check=True).stdout)
    existing = {asset["name"]: asset for asset in data.get("assets", [])}
    pending = []
    for path in files:
        asset = existing.get(path.name)
        digest = asset.get("digest") if asset else None
        if asset and ((digest == f"sha256:{file_hash(path)}") or (digest is None and asset.get("size") == path.stat().st_size)):
            continue
        pending.append(path)
    if pending:
        immutable = immutable_releases_enabled(repository)
        collisions = any(path.name in existing for path in pending)
        if collisions and immutable is not False:
            detail = "immutable" if immutable else "policy-unreadable"
            raise RuntimeError(f"{detail} per-run release already contains a different asset; publish under a new run ID")
        subprocess.run(["gh", "release", "upload", tag, "--repo", repository, "--clobber", *map(str, pending)], check=True)
        data = json.loads(subprocess.run(["gh", "api", f"repos/{repository}/releases/tags/{tag}"], text=True, capture_output=True, check=True).stdout)
    return [asset["browser_download_url"] for asset in data.get("assets", [])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render"); render.add_argument("--replays", type=Path, required=True); render.add_argument("--output", type=Path, required=True); render.add_argument("--run-id", required=True)
    publish = commands.add_parser("publish"); publish.add_argument("--directory", type=Path, required=True); publish.add_argument("--repository", required=True); publish.add_argument("--run-id", required=True); publish.add_argument("--links", type=Path, required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "render":
        build_media(arguments.replays, arguments.output, run_id=arguments.run_id)
    else:
        urls = publish_media(arguments.directory, arguments.repository, arguments.run_id)
        arguments.links.write_text(json.dumps({"run_id": arguments.run_id, "asset_urls": urls}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
