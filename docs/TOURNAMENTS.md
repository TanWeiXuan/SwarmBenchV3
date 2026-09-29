# Tournaments and ratings

The `Ranking Tournament` workflow runs at minute 17 every six hours; manual dispatch supports official/exhibition and small/default/large presets. Default matchmaking targets about eight opponents: three nearby-rating choices for each exploratory broad choice. Four deterministic scenarios are used per pairing, with exact A/B side swaps, yielding eight games.

Plans freeze engine/rules/tournament versions, source revision, controller content hashes, participant versions, pairings, scenario seeds, sides and balanced batches. Up to 19 independent compute jobs run in parallel, but games remain sequential inside each runner. The sixteen workers are internal to one game, never tournament entries.

Glicko-2 starts at rating 1500, RD 350 and volatility 0.06. Only win/draw/loss matters. Every participant is updated simultaneously from pre-period values. Exhibition aggregation validates identically but returns byte-for-byte unchanged ratings.

Each JSON batch self-hashes and is checked against its exact frozen slice. All batches must appear exactly once; game IDs, controller hashes, seeds, sides, survivor ranges, versions and final-state hashes must match. Current ratings must retain the frozen participant set and file hash. Any mismatch aborts before mutation. Official ratings and README top ten are written atomically and published through a serialized squash-only bot PR. Accepted-controller and tournament publication share `swarmbench-v3-rating-publication` concurrency.

The reporter owns one permanent Tournament Results Discussion. Its immutable RUNNING opener lists the frozen participants and starting ratings, schedule, seed, versions, revision, UTC start time, and Actions link. Later replies are authoritative.

Progress replies are posted around 20/40/60/80/100 percent of **games**, not artifact counts. The reporter reads JSON only from successful compute jobs and validates each batch against the frozen context and schedule before counting it. A jump across several milestones produces one current update. Replies include provisional matchup W-D-L, survivor totals, end reasons, and controller reliability/timing. They do not update ratings or execute controllers.

The final reply includes per-controller before/after ratings, deltas, RD, this-period W-D-L, community top ten, side-correct matchup details, closest survivor finish, largest rating-gap upset, and reliability statistics. Missing telemetry is marked unavailable; timing reports mean of unit means, worst unit p95, and maximum in milliseconds (not a pooled percentile). Tables are capped with a notice; complete batches remain in Actions artifacts. Exhibition ratings stay unchanged. Official figures are calculated results, not a claim that the publication PR has already merged. Detailed history stays in Discussions, not Git.

At most three candidate replays are selected by smallest survivor difference then stable game ID. Rendering is credential-free and cannot block rating finalization. Successful media is uploaded first as a 90-day Actions artifact. A separate trusted job creates an idempotent per-run, non-latest media release, uploads returned MP4/PNG/replay/manifest files, and appends the actual returned asset URLs. Per-run releases are compatible with immutable-release policies and never move a software tag.
