from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from swarmbench.competition import automation
from swarmbench.competition.ratings import RatingRecord
from swarmbench.competition.reporting import final_summary, initial_discussion_body, matchup_lines, progress_summary, reliability_lines, table
from swarmbench.competition.tournament import _batch_hash
from tests.test_competition import _fake_batches


@pytest.fixture
def context():
    root = Path(__file__).parents[1]
    return automation.prepare_plan(root / 'leaderboard/ratings.json', seed=42, mode='exhibition', size='small', run_id='123', repository='owner/repo', root=root)


def game(a='a', b='b', score=1.0, survivors_a=7, survivors_b=0, game_id='g1'):
    return dict(controller_a=a, controller_b=b, result_a=score, survivors_a=survivors_a, survivors_b=survivors_b, game_id=game_id, scenario_seed=42, reason='elimination')


def test_opener_has_frozen_metadata_and_participants(context):
    body = initial_discussion_body(context)
    for value in ('Initial status: RUNNING', 'https://github.com/owner/repo/actions/runs/123', context['plan_sha256'], context['started_at'], 'Starting rating ± RD', 'Community', 'Exhibition games never change ratings'):
        assert value in body


def test_matchups_correctly_combine_side_swaps():
    body = '\n'.join(matchup_lines([game(), game('b', 'a', 0.0, 0, 6, 'g2'), game(score=0.5)]))
    assert '| a | b | 3 | 2-1-0 | 20–0 |' in body


def test_final_reports_period_results_not_lifetime_counts():
    before = {'a': RatingRecord('a', 'A', 'x', rating=1400, wins=100, games=100), 'b': RatingRecord('b', 'B', 'y', rating=1600, built_in=True)}
    after = {'a': replace(before['a'], rating=1410), 'b': replace(before['b'], rating=1590)}
    outcome = SimpleNamespace(games=[game()], ratings_before=before, ratings_after=after)
    body = final_summary(outcome, 'official')
    assert '| a | 1400.0 | 1410.0 | +10.0 | 350.0 | 1-0-0 |' in body
    assert '200.0-point gap' in body
    assert 'publication PR merges' in body
    assert '| 1 | a | 1410.0 ± 350.0 |' in body
    assert '| 2 | b |' not in body
    exhibition = final_summary(SimpleNamespace(games=[game()], ratings_before=before, ratings_after=before), 'exhibition')
    assert 'ratings and RD are unchanged' in exhibition
    assert 'Largest rating rise' not in exhibition


def test_optional_telemetry_is_honest_and_timing_is_milliseconds():
    result = game()
    assert 'Telemetry available for 0/2' in '\n'.join(reliability_lines([result]))
    result['stats_a'] = dict(hard_failures=2, soft_misses=3, invalid_actions=4, units=[dict(mean=.001, p95=.002, max=.003)])
    result['stats_b'] = {'units': None}
    body = '\n'.join(reliability_lines([result]))
    assert 'Hard failures: 2' in body and 'invalid actions: 4' in body
    assert '1.00 ms' in body and '2.00 ms' in body and '3.00 ms' in body


def test_labels_cannot_inject_markdown_or_mentions(context):
    context['rating_snapshot']['controllers'][0]['display_name'] = '<script>|\n@everyone [link](url)'
    body = initial_discussion_body(context)
    assert '<script>' not in body and '@everyone' not in body and '[link]' not in body
    assert '&#124;' in body and '&lt;script&gt;' in body
    assert 'Showing 40 of 100' in '\n'.join(table(['X'], [['x']] * 100))


def test_progress_counts_games_not_artifacts(context):
    context['games'] = [{}] * 10
    context['batches'] = [[], []]
    body = progress_summary(context, [game()] * 3, 1)
    assert 'Progress: 30%' in body and '3/10 games' in body and '1/2 completed batches' in body


def progress_batches(context):
    plan, _ = automation.validate_context(context)
    batches = _fake_batches(plan)
    for batch in batches:
        batch.update({key: context[key] for key in ('run_id', 'repository', 'context_sha256')})
        batch['artifact_sha256'] = _batch_hash(batch)
    return plan, batches


def test_progress_rejects_foreign_and_tampered_batches(context):
    plan, batches = progress_batches(context)
    assert automation._validated_progress_batch(plan, context, batches[0], 0)
    foreign = deepcopy(batches[0])
    foreign['run_id'] = 'other'
    with pytest.raises(ValueError, match='context mismatch'):
        automation._validated_progress_batch(plan, context, foreign, 0)
    batches[0]['games'][0]['survivors_a'] = 0
    with pytest.raises(ValueError, match='integrity'):
        automation._validated_progress_batch(plan, context, batches[0], 0)


def test_live_report_skips_duplicate_catchup_posts(context, monkeypatch, tmp_path):
    _, batches = progress_batches(context)
    comments, downloads = [], []
    monkeypatch.setattr(automation, 'create_discussion', lambda _: dict(id='discussion', url='url'))
    monkeypatch.setattr(automation, 'add_discussion_comment', lambda _, body: comments.append(body))
    def api(args):
        if '/artifacts?' in args[-1]:
            return {'artifacts': [dict(name=f'tournament-batch-{index}') for index in range(len(batches))]}
        return {'jobs': [dict(name=f'Compute batch {index + 1} (untrusted controllers)', conclusion='success') for index in range(len(batches))]}
    monkeypatch.setattr(automation, '_gh_json', api)
    def download(ctx, artifact, index):
        downloads.append(index)
        return batches[index]
    monkeypatch.setattr(automation, '_download_batch', download)
    automation.live_report(context, run_id='123', output=tmp_path / 'discussion.json')
    assert len(comments) == 1 and 'Progress: 100%' in comments[0]
    assert downloads == list(range(len(batches)))
    assert json.loads((tmp_path / 'discussion.json').read_text())['id'] == 'discussion'


def test_live_report_does_not_count_artifacts_from_failed_jobs(context, monkeypatch, tmp_path):
    comments = []
    monkeypatch.setattr(automation, 'create_discussion', lambda _: dict(id='discussion', url='url'))
    monkeypatch.setattr(automation, 'add_discussion_comment', lambda _, body: comments.append(body))
    monkeypatch.setattr(automation, '_gh_json', lambda args: {'artifacts': [{'name': 'tournament-batch-0'}]} if '/artifacts?' in args[-1] else {'jobs': [{'name': 'Compute batch 1 (untrusted controllers)', 'conclusion': 'failure'}]})
    monkeypatch.setattr(automation, '_download_batch', lambda *args: pytest.fail('failed job artifact downloaded'))
    with pytest.raises(RuntimeError, match='compute failed'):
        automation.live_report(context, run_id='123', output=tmp_path / 'discussion.json')
    assert len(comments) == 1 and 'FAILED' in comments[0]


def test_archive_reader_only_reads_expected_json(context, monkeypatch):
    def download(*args, stdout, **kwargs):
        with zipfile.ZipFile(stdout, 'w') as archive:
            archive.writestr('../unexpected.py', 'raise RuntimeError()')
            archive.writestr('batch-0.json', '{"example": 1}')
    monkeypatch.setattr(automation.subprocess, 'run', download)
    assert automation._download_batch(context, {'id': 1}, 0) == {'example': 1}
    with pytest.raises(ValueError, match='missing'):
        automation._download_batch(context, {'id': 1}, 1)
    with pytest.raises(ValueError, match='too large'):
        automation._download_batch(context, {'id': 1, 'size_in_bytes': 129 * 1024 * 1024}, 0)


def test_live_report_reports_invalid_artifact_without_progress(context, monkeypatch, tmp_path):
    comments = []
    monkeypatch.setattr(automation, 'create_discussion', lambda _: dict(id='discussion', url='url'))
    monkeypatch.setattr(automation, 'add_discussion_comment', lambda _, body: comments.append(body))
    monkeypatch.setattr(automation, '_gh_json', lambda args: {'artifacts': [{'name': 'tournament-batch-0'}]} if '/artifacts?' in args[-1] else {'jobs': [{'name': 'Compute batch 1 (untrusted controllers)', 'conclusion': 'success'}]})
    monkeypatch.setattr(automation, '_download_batch', lambda *args: {})
    with pytest.raises(ValueError, match='context mismatch'):
        automation.live_report(context, run_id='123', output=tmp_path / 'discussion.json')
    assert len(comments) == 1 and 'FAILED' in comments[0] and 'Progress:' not in comments[0]
