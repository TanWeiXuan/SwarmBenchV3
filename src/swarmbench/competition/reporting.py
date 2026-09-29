"""Pure Markdown reports for permanent tournament Discussions.

Inputs are frozen context and validated results; reporting never runs controllers.
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import html
import math
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cell(value: Any) -> str:
    """Keep controller-provided labels from changing Markdown or pinging users."""
    text = " ".join(str(value).split())
    value = html.escape(text[:80] + ('…' if len(text) > 80 else ''))
    for character in "|`*_[]\\@":
        value = value.replace(character, f"&#{ord(character)};")
    return value


def table(headers: list[str], rows: list[list[str]], limit: int = 40) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[:limit])
    if len(rows) > limit:
        lines.extend(["", f"Showing {limit} of {len(rows)} rows; full results are in the run artifacts."])
    return lines


def metadata(context: dict[str, Any]) -> list[str]:
    run_url = f"https://github.com/{context['repository']}/actions/runs/{context['run_id']}"
    return [
        f"- Run: [{cell(context['run_id'])}]({run_url}) · {cell(context['mode'])} / {cell(context['size'])}",
        f"- Seed: `{context['seed']}` · engine `{cell(context['engine_version'])}` · rules `{cell(context['ruleset_version'])}`",
        f"- Schedule: {len(context['controller_hashes'])} controllers · {len(context['pairings'])} pairings · {len(context['games'])} games · {len(context['batches'])} batches",
        f"- Started (UTC): {cell(context.get('started_at', 'not recorded'))}",
        f"- Source revision: `{cell(context['source_revision'])}`",
        f"- Frozen plan: `{context['plan_sha256']}`",
    ]


def initial_discussion_body(context: dict[str, Any]) -> str:
    records = context['rating_snapshot']['controllers']
    rows = [[cell(item['controller_id']), cell(item['display_name']), 'Baseline' if item['built_in'] else 'Community', f"{item['rating']:.1f} ± {item['deviation']:.1f}"] for item in sorted(records, key=lambda item: item['controller_id'])]
    return "\n".join([
        "## Initial status: RUNNING", "", *metadata(context), "",
        "The latest bot reply is authoritative. Progress is posted at roughly 20% intervals from validated completed games; results remain provisional until every batch passes final validation.", "",
        "Official rating changes require the publication PR to merge. Exhibition games never change ratings.", "",
        "### Frozen participants", "", *table(['Controller', 'Name', 'Type', 'Starting rating ± RD'], rows), "",
        "MP4 highlights, PNG previews, and replay downloads are posted separately after rendering succeeds.",
    ])


def matchup_lines(games: list[dict[str, Any]]) -> list[str]:
    pairs = defaultdict(lambda: [0, 0, 0, 0, 0])
    for game in games:
        left, right = sorted((game['controller_a'], game['controller_b']))
        a_is_left = game['controller_a'] == left
        score = game['result_a'] if a_is_left else 1 - game['result_a']
        record = pairs[left, right]
        record[{1.0: 0, 0.5: 1, 0.0: 2}[score]] += 1
        record[3] += game['survivors_a' if a_is_left else 'survivors_b']
        record[4] += game['survivors_b' if a_is_left else 'survivors_a']
    rows = [[cell(left), cell(right), str(sum(values[:3])), '-'.join(map(str, values[:3])), f'{values[3]}–{values[4]}'] for (left, right), values in sorted(pairs.items())]
    return ['### Matchups', '', 'W-D-L is from the left controller’s perspective, across both sides. Survivors are summed, not a scoring rule.', '', *table(['Left controller', 'Right controller', 'Games', 'Left W-D-L', 'Survivors L–R'], rows)]


def reliability_lines(games: list[dict[str, Any]]) -> list[str]:
    stats = [game.get(f'stats_{side}') for game in games for side in ('a', 'b')]
    valid = [item for item in stats if isinstance(item, dict) and all(type(item.get(key)) is int and item[key] >= 0 for key in ('hard_failures', 'soft_misses', 'invalid_actions'))]
    lines = ['### Controller reliability', '', f'Telemetry available for {len(valid)}/{len(stats)} controller-game sides.']
    if valid:
        lines.extend(['', f"- Hard failures: {sum(item['hard_failures'] for item in valid)} · soft deadline misses: {sum(item['soft_misses'] for item in valid)} · invalid actions: {sum(item['invalid_actions'] for item in valid)}"])
    units = [unit for item in valid if isinstance(item.get('units'), list) for unit in item['units'] if isinstance(unit, dict) and all(type(unit.get(key)) in (int, float) and math.isfinite(unit[key]) and unit[key] >= 0 for key in ('mean', 'p95', 'max'))]
    if units:
        lines.append(f"- Step timing: mean of unit means {1000 * sum(unit['mean'] for unit in units) / len(units):.2f} ms · worst unit p95 {1000 * max(unit['p95'] for unit in units):.2f} ms · maximum {1000 * max(unit['max'] for unit in units):.2f} ms ({len(units)} unit summaries).")
    else:
        lines.extend(['', 'Step timing: unavailable.'])
    return lines


def result_lines(games: list[dict[str, Any]]) -> list[str]:
    results = Counter(game['result_a'] for game in games)
    reasons = Counter(str(game.get('reason', 'unavailable')) for game in games)
    return [f"{len(games)} games · {results[1.0]} A wins / {results[0.5]} draws / {results[0.0]} B wins", '', 'End reasons: ' + (', '.join(f'{cell(reason)}: {count}' for reason, count in sorted(reasons.items())[:10]) or 'none')]


def progress_summary(context: dict[str, Any], games: list[dict[str, Any]], ready: int) -> str:
    total = len(context['games'])
    percent = len(games) * 100 // total if total else 100
    return '\n'.join([f'## Progress: {percent}%', '', f"{len(games)}/{total} games validated from {ready}/{len(context['batches'])} completed batches.", '', *result_lines(games), '', *matchup_lines(games), '', *reliability_lines(games), '', 'Provisional results only. Ratings remain unchanged until complete-period validation and official publication.'])


def final_summary(outcome: Any, mode: str, context: dict[str, Any] | None = None) -> str:
    games = list(outcome.games)
    before, after = outcome.ratings_before, outcome.ratings_after
    counts = defaultdict(lambda: [0, 0, 0])
    for game in games:
        counts[game['controller_a']][{1.0: 0, 0.5: 1, 0.0: 2}[game['result_a']]] += 1
        counts[game['controller_b']][{0.0: 0, 0.5: 1, 1.0: 2}[game['result_a']]] += 1
    ordered = sorted(after, key=lambda key: (-after[key].rating, key))
    rows = [[cell(key), f'{before[key].rating:.1f}', f'{after[key].rating:.1f}', f'{after[key].rating - before[key].rating:+.1f}', f'{after[key].deviation:.1f}', '-'.join(map(str, counts[key]))] for key in ordered]
    lines = ['## Status: COMPLETE', '', *(metadata(context) if context else [f'Mode: {cell(mode)}']), '', f'Completed validation (UTC): {utc_now()}', '', 'Every planned game passed complete-period validation.', '', *result_lines(games), '', '### Rating period', '', 'Official results below are calculated; the leaderboard changes only when the rating publication PR merges.' if mode == 'official' else 'Exhibition: ratings and RD are unchanged. W-D-L below covers this tournament only.', '', *table(['Controller', 'Before', 'After', 'Δ', 'RD after', 'Period W-D-L'], rows), '', '### Highlights', '']
    if mode == 'official' and ordered:
        gain = max(ordered, key=lambda key: after[key].rating - before[key].rating)
        loss = min(ordered, key=lambda key: after[key].rating - before[key].rating)
        lines.append(f'- Largest rating rise: {cell(gain)} ({after[gain].rating - before[gain].rating:+.1f}); largest fall: {cell(loss)} ({after[loss].rating - before[loss].rating:+.1f}).')
    if games:
        closest = min(games, key=lambda game: (abs(game['survivors_a'] - game['survivors_b']), game['game_id']))
        lines.append(f"- Closest survivor finish: {cell(closest['controller_a'])} vs {cell(closest['controller_b'])}, {closest['survivors_a']}–{closest['survivors_b']} survivors (game `{cell(closest['game_id'])}`, seed `{closest['scenario_seed']}`).")
        upsets = []
        for game in games:
            if game['result_a'] == 0.5:
                continue
            winner, loser = (game['controller_a'], game['controller_b']) if game['result_a'] == 1.0 else (game['controller_b'], game['controller_a'])
            gap = before[loser].rating - before[winner].rating
            if gap > 0:
                upsets.append((gap, game['game_id'], winner, loser))
        if upsets:
            gap, game_id, winner, loser = max(upsets)
            lines.append(f'- Biggest upset by starting rating: {cell(winner)} beat {cell(loser)} across a {gap:.1f}-point gap (game `{cell(game_id)}`).')
        else:
            lines.append('- No lower-rated controller beat a higher-rated opponent.')
    community = [key for key in ordered if not after[key].built_in]
    lines.extend(['', '### Community standings' + (' (calculated period result)' if mode == 'official' else ' (unchanged)'), '', *table(['Rank', 'Controller', 'Rating ± RD'], [[str(index), cell(key), f'{after[key].rating:.1f} ± {after[key].deviation:.1f}'] for index, key in enumerate(community, 1)], limit=10), '', *matchup_lines(games), '', *reliability_lines(games), '', '### Replays and media', '', 'Batch artifacts contain replay candidates. MP4 highlights and PNG previews are rendered separately; a later media reply provides durable download links if publication succeeds. Media failure does not invalidate these results.'])
    return '\n'.join(lines)
