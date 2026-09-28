from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_untrusted_workflows_have_read_only_repository_permissions() -> None:
    submission = (ROOT / ".github/workflows/submission-validation.yml").read_text(encoding="utf-8")
    tournament = (ROOT / ".github/workflows/tournament.yml").read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in submission
    compute = tournament.split("  compute:", 1)[1].split("  render-media:", 1)[0]
    assert "permissions:\n      contents: read" in compute
    assert "SWARMBENCH_APP_PRIVATE_KEY" not in compute
    assert "max-parallel: 19" in compute
    assert 'cron: "17 */6 * * *"' in tournament
    assert '"### Tournament media\\n\\n" + (.asset_urls | map("- " + .) | join("\\n"))' in tournament


def test_submission_calibration_is_bounded_and_clean_prs_merge_directly() -> None:
    validation = (ROOT / ".github/workflows/submission-validation.yml").read_text(encoding="utf-8")
    reporter = (ROOT / ".github/workflows/submission-reporter.yml").read_text(encoding="utf-8")
    assert "--match-workers 2" in validation
    assert "pull.mergeable_state === 'clean'" in reporter
    assert "merge_method: 'squash', sha: run.head_sha" in reporter


def test_trusted_workflows_do_not_checkout_untrusted_pr_head() -> None:
    accepted = (ROOT / ".github/workflows/submission-accepted.yml").read_text(encoding="utf-8")
    reporter = (ROOT / ".github/workflows/submission-reporter.yml").read_text(encoding="utf-8")
    assert "pull_request_target" in accepted and "ref: main" in accepted
    assert "ref: ${{ github.event.pull_request.head" not in accepted
    assert "actions/checkout" not in reporter
    tournament = (ROOT / ".github/workflows/tournament.yml").read_text(encoding="utf-8")
    assert "swarmbench-v3-rating-publication" in accepted
    assert "swarmbench-v3-rating-publication" in tournament


def test_repository_template_does_not_trigger_submission_publication() -> None:
    accepted = (ROOT / ".github/workflows/submission-accepted.yml").read_text(encoding="utf-8")
    reporter = (ROOT / ".github/workflows/submission-reporter.yml").read_text(encoding="utf-8")
    assert '"!submissions/example/controller.py"' in accepted
    assert "file.filename.endsWith('.py')" in reporter
    assert "file.filename !== 'submissions/example/controller.py'" in reporter


def test_runtime_has_no_v2_checkout_dependency() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "SwarmBenchV2" not in text
        assert "../SwarmBenchV2" not in text
