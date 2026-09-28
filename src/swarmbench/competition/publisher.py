"""Trusted current-state publication; never imports submission code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .ratings import RatingRecord, load_ratings, save_ratings
from .submission import CALIBRATION_SCHEMA


def update_leaderboard(readme: Path, records: dict[str, RatingRecord]) -> None:
    text = readme.read_text(encoding="utf-8")
    start, end = "<!-- LEADERBOARD_START -->", "<!-- LEADERBOARD_END -->"
    if start not in text or end not in text:
        raise ValueError("README leaderboard markers are missing")
    ranked = sorted(records.values(), key=lambda item: (-item.rating, item.controller_id))[:10]
    lines = [start, "| Rank | Controller | Author | Rating | RD | W | D | L | Games |", "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    lines.extend(f"| {index} | {record.display_name} | {record.author} | {record.rating:.0f} | {record.deviation:.0f} | {record.wins} | {record.draws} | {record.losses} | {record.games} |" for index, record in enumerate(ranked, 1))
    lines.append(end)
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    temporary = readme.with_name(readme.name + ".tmp")
    temporary.write_text(before + "\n".join(lines) + after, encoding="utf-8")
    os.replace(temporary, readme)


def apply_calibration(artifact_path: Path, ratings_path: Path, readme: Path, expected_path: str, expected_sha: str) -> None:
    if artifact_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("calibration artifact is too large")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid numeric constant: {value}")))
    integrity = artifact.pop("artifact_sha256", None)
    if integrity != hashlib.sha256(json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()).hexdigest():
        raise ValueError("calibration result integrity mismatch")
    if artifact.get("schema") != CALIBRATION_SCHEMA or artifact.get("submission_path") != expected_path or artifact.get("head_sha") != expected_sha:
        raise ValueError("calibration publication identity mismatch")
    parts = Path(expected_path).parts
    if len(parts) != 3 or parts[0] != "submissions" or Path(parts[2]).suffix != ".py":
        raise ValueError("invalid accepted submission path")
    controller_id = f"{parts[1]}/{Path(parts[2]).stem}"
    records = load_ratings(ratings_path)
    if controller_id in records:
        raise ValueError("accepted controller already has a rating")
    records[controller_id] = RatingRecord(controller_id, Path(parts[2]).stem.replace("_", " ").title(), parts[1], artifact["provisional_rating"], artifact["deviation"], artifact["volatility"], artifact["wins"], artifact["draws"], artifact["losses"], artifact["match_count"], artifact["controller_sha256"], False)
    save_ratings(records, ratings_path)
    update_leaderboard(readme, records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--ratings", type=Path, required=True)
    parser.add_argument("--readme", type=Path, required=True)
    parser.add_argument("--expected-path-file", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    arguments = parser.parse_args(argv)
    apply_calibration(arguments.artifact, arguments.ratings, arguments.readme, arguments.expected_path_file.read_text(encoding="utf-8").strip(), arguments.expected_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
