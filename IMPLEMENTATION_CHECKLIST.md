# SwarmBenchV3 implementation checklist

- [x] Read `SWARMBENCH_V3_CODEX_PROMPT.md` completely.
- [x] Inspect the V2 competition, validation, runner, replay, workflow, documentation, and tests; record its source revision.
- [x] Port the reusable competition foundation with V3 schemas and fresh ratings.
- [x] Define the immutable V3 public API and centralized official ruleset.
- [x] Implement and test map generation, visibility, movement/collisions, combat, radio, and deterministic tick ordering.
- [x] Implement per-unit fresh-process and Docker worker backends plus the three simple baselines.
- [x] Integrate one-file validation, tournaments, artifact validation, ratings, and publication tooling.
- [x] Implement authoritative compressed replays, reconstruction, MP4-first rendering, unit perspective, and media manifests.
- [x] Port/adapt GitHub workflows and write contributor, security, gameplay, submission, tournament, and migration documentation.
- [x] Run unit/acceptance tests, sample side-swapped matches, rendering/FFprobe checks, and runtime diagnostics.
- [x] Record verified functionality, generated artifacts, measurements, and remaining local/remote blockers.

Implementation is complete locally. Docker command construction/isolation is tested, but live Docker execution is blocked on this host by permission to `/var/run/docker.sock`; see `docs/VALIDATION_REPORT.md`.
