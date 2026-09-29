from copy import deepcopy
import hashlib
import json
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest

from swarmbench.competition.glicko2 import GlickoRating, update_rating
from swarmbench.competition.media import immutable_releases_enabled
from swarmbench.competition.automation import reconcile_current_ratings
from swarmbench.competition.ratings import RatingRecord, apply_rating_period, load_ratings, save_ratings
from swarmbench.competition.submission import aggregate_calibration, calibration_opponents, calibration_seed, validate_structure
from swarmbench.controllers.baselines import BASELINE_NAMES
from swarmbench.competition.tournament import _batch_hash, aggregate_batches, create_plan


def test_reference_glicko2_example() -> None:
    value = update_rating(GlickoRating(1500, 200, 0.06), [(GlickoRating(1400, 30), 1), (GlickoRating(1550, 100), 0), (GlickoRating(1700, 300), 0)])
    assert value.rating == pytest.approx(1464.06, abs=0.02)
    assert value.deviation == pytest.approx(151.52, abs=0.02)


def _fake_batches(plan):
    games = {game.game_id: game for game in plan.games}
    batches = []
    for index, ids in enumerate(plan.batches):
        results = []
        for game_id in ids:
            game = games[game_id]
            results.append({"game_id": game_id, "pairing_id": game.pairing_id, "controller_a": game.controller_a, "controller_b": game.controller_b, "controller_hash_a": plan.controller_hashes[game.controller_a], "controller_hash_b": plan.controller_hashes[game.controller_b], "scenario_seed": game.scenario_seed, "result_a": 0.5, "survivors_a": 8, "survivors_b": 8, "reason": "time_limit_equal_survivors", "final_state_hash": "0" * 64})
        batch = {"format_version": 3, "engine_version": "3.0.0", "ruleset_version": "v3-prototype-1", "plan_sha256": plan.to_dict()["plan_sha256"], "tournament_seed": plan.seed, "source_revision": plan.source_revision, "batch_index": index, "expected_game_ids": sorted(ids), "games": results, "replay_artifacts": []}
        batch["artifact_sha256"] = _batch_hash(batch)
        batches.append(batch)
    return batches


def test_side_swaps_exhibition_immutability_and_tamper_rejection() -> None:
    root = Path(__file__).parents[1]
    records = load_ratings(root / "leaderboard/ratings.json")
    plan = create_plan(records, 42, mode="exhibition", size="small", root=root)
    assert len(plan.games) == len(plan.pairings) * 2
    for first, second in zip(plan.games[::2], plan.games[1::2]):
        assert first.scenario_seed == second.scenario_seed
        assert (first.controller_a, first.controller_b) == (second.controller_b, second.controller_a)
    batches = _fake_batches(plan)
    outcome = aggregate_batches(plan, batches, records)
    assert outcome.ratings_after == records
    tampered = deepcopy(batches)
    tampered[0]["games"][0]["result_a"] = 1.0
    with pytest.raises(ValueError, match="integrity"):
        aggregate_batches(plan, tampered, records)


def test_simultaneous_rating_update_uses_period_start_values() -> None:
    records = {"a": RatingRecord("a", "A", "x"), "b": RatingRecord("b", "B", "y"), "c": RatingRecord("c", "C", "z")}
    forward = apply_rating_period(records, [("a", "b", 1.0), ("b", "c", 1.0)])
    reverse = apply_rating_period(records, [("b", "c", 1.0), ("a", "b", 1.0)])
    assert forward == reverse


def test_rating_reconciliation_preserves_newly_accepted_controller() -> None:
    snapshot = {"a": RatingRecord("a", "A", "x")}
    current = {**snapshot, "new/controller": RatingRecord("new/controller", "New", "new", rating=1600)}
    updated = {"a": RatingRecord("a", "A", "x", rating=1510)}
    merged = reconcile_current_ratings(current, snapshot, updated)
    assert merged["a"].rating == 1510 and merged["new/controller"].rating == 1600
    with pytest.raises(ValueError, match="frozen participant"):
        reconcile_current_ratings({"a": RatingRecord("a", "A", "x", rating=1499)}, snapshot, updated)


def test_repository_template_is_not_classified_as_submission(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    changed = "A\tsubmissions/README.md\nA\tsubmissions/example/controller.py\nA\tsrc/swarmbench/api.py\n"
    monkeypatch.setattr(
        "swarmbench.competition.submission.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=changed),
    )
    assert validate_structure("base", "maintainer", tmp_path) == {"submission_path": None}


def test_submission_cannot_be_mixed_with_other_changes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    changed = "A\tsubmissions/contributor/controller.py\nM\tREADME.md\n"
    monkeypatch.setattr(
        "swarmbench.competition.submission.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=changed),
    )
    with pytest.raises(ValueError, match="exactly"):
        validate_structure("base", "contributor", tmp_path)


def test_calibration_matches_run_in_parallel_and_keep_canonical_order(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).parents[1]
    lock = Lock()
    overlap = Event()
    active = 0
    peak = 0

    def fake_run_match(left, right, *, seed, backend):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active >= 2:
                overlap.set()
        assert overlap.wait(timeout=1.0), "calibration matches did not overlap"
        with lock:
            active -= 1
        stats = {"units": []}
        return SimpleNamespace(
            winner=None,
            replay=SimpleNamespace(result={"final_state_hash": f"{seed:064x}"}),
            stats_a=stats,
            stats_b=stats,
        )

    monkeypatch.setattr("swarmbench.competition.submission.run_match", fake_run_match)
    submission = root / "submissions/TanWeiXuan/lantern_phalanx.py"
    result = calibration_seed(
        submission,
        "submissions/TanWeiXuan/lantern_phalanx.py",
        "a" * 40,
        0,
        backend="local",
        match_workers=2,
        ratings_path=root / "leaderboard/ratings.json",
    )

    records = load_ratings(root / "leaderboard/ratings.json")
    expected_opponents = [
        *BASELINE_NAMES,
        *sorted(
            controller_id
            for controller_id, record in records.items()
            if not record.built_in and controller_id != "TanWeiXuan/lantern_phalanx"
        ),
    ]
    assert peak == 2
    assert [opponent["controller_id"] for opponent in result["opponents"]] == expected_opponents
    assert any(not opponent["built_in"] for opponent in result["opponents"])
    assert [(game["opponent"], game["side"]) for game in result["games"]] == [
        (opponent, side)
        for opponent in expected_opponents
        for side in ("ab", "ba")
    ]


def test_calibration_rejects_nonpositive_worker_count() -> None:
    root = Path(__file__).parents[1]
    with pytest.raises(ValueError, match="positive"):
        calibration_seed(
            root / "submissions/example/controller.py",
            "submissions/example/controller.py",
            "a" * 40,
            0,
            backend="local",
            match_workers=0,
        )


def test_calibration_rejects_community_source_that_disagrees_with_ratings(tmp_path: Path) -> None:
    ratings_path = tmp_path / "leaderboard/ratings.json"
    controller_path = tmp_path / "submissions/alice/controller.py"
    ratings_path.parent.mkdir()
    controller_path.parent.mkdir(parents=True)
    controller_path.write_text("from swarmbench import BaseUnitController\nclass UnitController(BaseUnitController):\n    pass\n", encoding="utf-8")
    records = {
        name: RatingRecord(name, name, "SwarmBench", built_in=True)
        for name in BASELINE_NAMES
    }
    records["alice/controller"] = RatingRecord(
        "alice/controller",
        "Controller",
        "alice",
        version_sha="0" * 64,
    )
    save_ratings(records, ratings_path)

    with pytest.raises(ValueError, match="hash mismatch"):
        calibration_opponents("submissions/new/controller.py", ratings_path)


def test_calibration_aggregate_uses_frozen_community_ratings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    submission_path = "submissions/TanWeiXuan/lantern_phalanx.py"
    head_sha = "b" * 40

    def fake_run_match(left, right, *, seed, backend):
        stats = {"units": []}
        return SimpleNamespace(
            winner=None,
            replay=SimpleNamespace(result={"final_state_hash": f"{seed:064x}"}),
            stats_a=stats,
            stats_b=stats,
        )

    monkeypatch.setattr("swarmbench.competition.submission.run_match", fake_run_match)
    artifacts = []
    for seed_index in range(4):
        artifact = calibration_seed(
            submission_path,
            submission_path,
            head_sha,
            seed_index,
            backend="local",
            ratings_path=root / "leaderboard/ratings.json",
        )
        artifacts.append(artifact)
        (tmp_path / f"calibration-seed-{seed_index}.json").write_text(json.dumps(artifact), encoding="utf-8")

    result = aggregate_calibration(tmp_path, submission_path, submission_path, head_sha)
    expected_matches = [
        (GlickoRating(opponent["rating"], opponent["deviation"], opponent["volatility"]), 0.5)
        for _ in range(4)
        for opponent in artifacts[0]["opponents"]
        for _ in range(2)
    ]
    expected = update_rating(GlickoRating(), expected_matches)

    assert result["opponent_count"] == len(artifacts[0]["opponents"])
    assert result["match_count"] == 8 * result["opponent_count"]
    assert result["provisional_rating"] == pytest.approx(expected.rating)
    assert result["deviation"] == pytest.approx(expected.deviation)

    tampered = deepcopy(artifacts[1])
    tampered["opponents"][0]["rating"] += 1.0
    tampered["opponent_snapshot_sha256"] = hashlib.sha256(
        json.dumps(tampered["opponents"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    core = {key: value for key, value in tampered.items() if key != "artifact_sha256"}
    tampered["artifact_sha256"] = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (tmp_path / "calibration-seed-1.json").write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="snapshots disagree"):
        aggregate_calibration(tmp_path, submission_path, submission_path, head_sha)


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [(0, '{"enabled": true}', True), (0, '{"enabled": false}', False), (1, "", None), (0, "not-json", None)],
)
def test_immutable_release_policy_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stdout: str,
    expected: bool | None,
) -> None:
    monkeypatch.setattr(
        "swarmbench.competition.media.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout=stdout),
    )
    assert immutable_releases_enabled("owner/repository") is expected
