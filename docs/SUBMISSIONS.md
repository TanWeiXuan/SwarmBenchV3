# Submission and validation guide

Submit exactly one non-symlink file:

```text
submissions/<github-login>/<controller-name>.py
```

The maximum is 5 MiB. The current V3 controller image deliberately supplies the Python 3.12 standard library and `swarmbench`; external assets, downloads and undeclared packages are unavailable. Keep a controller understandable and comfortably below the deadline.

The required workflow checks PR shape, source/API, a short Docker runtime/baseline smoke with no win-rate or aggression threshold, and four deterministic calibration shards. Each shard plays every V3 baseline and every accepted community controller on both sides of the same scenarios, excluding the submitted controller itself. The opponent roster comes from the checked-in rating state; community source hashes must match that state, and current opponent Glicko-2 values are frozen into every artifact. Up to two matches run concurrently on each isolated runner, while results retain their canonical opponent/side order regardless of completion order. The aggregate is bound to submission path, file SHA, PR head SHA and the identical opponent snapshot from all four shards before computing a provisional rating. `Submission Gate` is the required check. Defensive legal controllers may draw and are not rejected for low exploration, packet counts or win rate.

A trusted workflow-run reporter uses a short-lived GitHub App token to maintain one sticky status comment and squash-merge only the exact successfully validated SHA. After merge, another trusted job checks out `main`, downloads the matching primitive calibration artifact and opens a serialized current-state ratings PR. No trusted job checks out a PR head.

Local commands use the unsandboxed development backend unless `--backend docker` is selected:

```bash
python -m swarmbench validate submissions/you/controller.py
docker build -t swarmbench-v3-controller -f Dockerfile.controller .
python -m swarmbench validate submissions/you/controller.py --backend docker
```
