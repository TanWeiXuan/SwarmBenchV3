# Local validation report

Validated on 2026-09-28 with Python 3.12.3 and FFmpeg 6.1.1/libx264.

## Automated checks

- `python -m pytest`: 47 passed.
- `python -m compileall -q src tests`: passed.
- All five workflow YAML files parsed with PyYAML.
- `pip check` reported no broken requirements.
- `git diff --check` passed.
- Thirty generator seeds were checked for deterministic identical output, clearance-valid connected spawns and opponent separation.
- Replay tests compare deterministic gzip bytes, every reconstructed observation hash and the final canonical engine-state hash.
- A diagnostic six-game frozen exhibition ran through prepare, per-batch compute, artifact self-hash validation and final aggregation without modifying ratings.
- Media selection chose exactly three replays by the documented stable rule and produced a hashed version-3 manifest.

## Final-code full matches

Each row is a full 1,200-boundary / 120-s match with sixteen persistent local worker processes. Side swaps use the same generated scenario. Local processes validate semantics and timing but are not a hostile-code sandbox.

| Seed / side | A | B | Result | Survivors | Wall time | First hit | Per-unit mean range | Max unit p95 | Max response | Soft misses | FF hits | Radio sent / delivered |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 42 / AB | rush | radio_rush | B, time | 4–5 | 11.327 s | 12.4 s | 0.183–0.769 ms | 1.374 ms | 23.267 ms | 0 | 3 | 151 / 660 |
| 42 / BA | radio_rush | rush | A, time | 7–4 | 15.965 s | 13.5 s | 0.111–0.800 ms | 1.373 ms | 97.271 ms | 0 | 0 | 240 / 1,423 |
| 7 / AB | spread_rush | radio_rush | draw, time | 4–4 | 11.697 s | 12.4 s | 0.140–1.088 ms | 1.273 ms | 91.869 ms | 0 | 0 | 165 / 366 |
| 7 / BA | radio_rush | spread_rush | A, time | 8–4 | 16.624 s | 12.4 s | 0.235–0.837 ms | 1.319 ms | 114.790 ms | 3 | 1 | 229 / 490 |

The final sample had a 0% no-contact rate and 25% draw rate. One match exercised the specified soft-deadline discard three times; it did not become a hard failure. Packets sent/delivered are traffic measurements only and do not establish strategic value. A separate no-radio `rush`/`spread_rush` seed-99 match drew 5–5 in 11.588 s. `/usr/bin/time -v` reported 77,084 KiB maximum resident set size for that complete command; this is command-level host measurement, not a per-container sum.

## Rendering artifacts

Local ignored artifacts are under `out/`:

- `final-42-ab.json.gz` and the other `final-*.json.gz` authoritative replays;
- `final-demo.mp4` and `final-demo.png`, a 15.05 s 1200×880 spectator clip/poster;
- `final-unit-A3.mp4` and its poster, a 1200×880 information-bounded unit view;
- `final-preview.gif`, a 600×440, 10 FPS, 12-second bounded preview;
- `automation/media/`, three deterministic MP4/PNG/replay selections and `media-manifest.json`.

FFprobe confirmed H.264, `yuv420p`, 1200×880. Explicit missing-FFmpeg behavior is covered by a test and raises an actionable error.

## Remote validation and remaining notes

- The `setup-smoke-2` exhibition ([run 36451543285](https://github.com/TanWeiXuan/SwarmBenchV3/actions/runs/36451543285)) completed all six Docker compute batches on GitHub-hosted runners. This live-tested the sixteen-container match path, artifact validation and fail-closed aggregation with the repository's configured App credentials.
- The same run updated the permanent `Tournament Results` Discussion, rendered three MP4/PNG selections and published ten durable assets to the non-latest [`tournament-media-run-36451543285`](https://github.com/TanWeiXuan/SwarmBenchV3/releases/tag/tournament-media-run-36451543285) release. Its final link-comment step exposed a `jq` grouping bug; the assets and tournament result were unaffected, and the corrected expression is regression-tested.
- The earlier `setup-smoke-1` run left an empty `tournament-media-run-36450562146` release after the minimally privileged App could not read the repository-level immutable-release setting. Publication now treats that setting as best-effort when creating a new release, while continuing to fail closed on conflicting assets when immutability is enabled or cannot be determined.
- Docker 29.1.3 remains inaccessible to the local desktop user through `/var/run/docker.sock`; Docker-backed execution is therefore verified remotely, while the local process runner remains the development and test path.
- The active `Protect main` ruleset and repository Actions/merge permissions match the V2 settings. GitHub currently emits maintenance warnings for the App-token action's deprecated `app-id` input and for JavaScript actions moving from Node 20 to Node 24; neither affected the validated tournament.
