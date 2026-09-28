# SwarmBenchV3

SwarmBenchV3 is a deterministic, two-dimensional, decentralized search-and-destroy benchmark. Two teams field eight identical disc units. The same submitted Python file is instantiated in eight independent workers; there is no team controller or shared controller memory. Units discover an unknown occupancy-grid arena, see locally, communicate with one optional 64-bit radio packet per control step, and use deterministic hitscan weapons.

The authoritative engine is headless. Replays are versioned compressed JSON, and MP4 rendering is a separate consumer that cannot change match timing or outcomes.

## Quick start

Python 3.12 is supported. Use a V3-only environment:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,competition,render]'
.venv/bin/python -m pytest
.venv/bin/python -m swarmbench arena --seed 42 --render out/arena.png
.venv/bin/python -m swarmbench validate submissions/example/controller.py
.venv/bin/python -m swarmbench match --controller-a rush --controller-b radio_rush \
  --seed 42 --replay out/match.json.gz
.venv/bin/python -m swarmbench render out/match.json.gz --output out/match.mp4
.venv/bin/python -m swarmbench render out/match.json.gz --output out/unit-A3.mp4 \
  --perspective unit --team A --unit-id 3 --fov
.venv/bin/python -m swarmbench render out/match.json.gz --output out/preview.gif \
  --readme-preview --start 20 --duration 12 --fps 10 --width 600
.venv/bin/python -m swarmbench tournament --mode exhibition --size small --seed 42
```

The local worker backend is the default for development and is **not a hostile-code sandbox**. Official validation and tournaments build `Dockerfile.controller` and create sixteen separate locked-down containers per match.

## Game at a glance

- Arena: 120 m × 80 m, static unknown 1 m occupancy cells.
- Runtime: 120 simulation seconds, 20 Hz physics and 10 Hz control.
- Units: radius 0.4 m, 4 m/s, 8 m/s², independently directed movement and facing.
- Sensors: 18 m / 120° main vision plus 3 m all-around awareness; walls occlude both.
- Weapon: 22 m deterministic hitscan, 20 damage, six rounds, 0.8 s interval, 2 s reload; friendly fire is enabled.
- Radio: one optional unsigned 64-bit broadcast, 30 m snapshot range, reliable one-step latency, friendly only, through walls.
- Win: immediate elimination; at time, survivor count only; equal survivors draw.

The exact rules, scheduling order, geometry and tie conventions are in [GAME_SPEC.md](docs/GAME_SPEC.md). The public observation/action surface is in [CONTROLLER_API.md](docs/CONTROLLER_API.md).

## Write and submit a controller

```python
from swarmbench import Action, BaseUnitController, Observation, UnitInfo


class UnitController(BaseUnitController):
    def initialize(self, info: UnitInfo) -> None:
        self.unit_id = info.unit_id

    def step(self, observation: Observation) -> Action:
        return Action(
            desired_velocity=(0.0, 0.0),
            desired_heading=observation.self_state.heading,
        )
```

One file at `submissions/<github-login>/<controller-name>.py` supplies all eight independent instances. See [SUBMISSIONS.md](docs/SUBMISSIONS.md). Built-ins are deliberately simple: `rush`, `spread_rush`, and `radio_rush`.

## Leaderboard

V3 starts fresh; no V2 ratings or history are carried over.

<!-- LEADERBOARD_START -->
| Rank | Controller | Author | Rating | RD | W | D | L | Games |
| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | Radio Rush | SwarmBench | 1500 | 350 | 0 | 0 | 0 | 0 |
| 2 | Rush | SwarmBench | 1500 | 350 | 0 | 0 | 0 | 0 |
| 3 | Spread Rush | SwarmBench | 1500 | 350 | 0 | 0 | 0 | 0 |
<!-- LEADERBOARD_END -->

Tournament design is documented in [TOURNAMENTS.md](docs/TOURNAMENTS.md); replay and MP4 behavior in [REPLAYS_AND_MEDIA.md](docs/REPLAYS_AND_MEDIA.md); security boundaries in [SECURITY.md](docs/SECURITY.md). Repository administrators should follow [REPOSITORY_SETUP.md](docs/REPOSITORY_SETUP.md).

SwarmBenchV3 is available under the [MIT License](LICENSE).
