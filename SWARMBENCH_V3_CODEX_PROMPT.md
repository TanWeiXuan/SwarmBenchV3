# Implement SwarmBenchV3

You have access to an existing `SwarmBenchV2` checkout and an empty `SwarmBenchV3` directory. Implement a complete, runnable first version of SwarmBenchV3 in the new directory. Do not stop after writing a design document or scaffolding.

## 1. Workspace, scope, and reuse

Treat V2 as a read-only reference. Locate both directories, inspect their existing instructions, and make every implementation change inside V3. Do not alter V2, copy its `.git` directory, copy credentials, or introduce runtime imports/path dependencies on V2. V3 must install and work independently. Use a separate virtual environment so the two projects do not accidentally import each other's package.

First inspect V2's README, game/API/security/submission/tournament documentation, `.github/workflows`, controller runner, competition modules, replay format, renderer, and relevant tests. Record the local V2 source commit in a short migration note. Reuse its sound tournament, rating, validation, reporting, and rendering infrastructure rather than unnecessarily rewriting it. Preserve applicable licensing/attribution. Inspect the actual local implementation instead of assuming its behavior from filenames.

The new gameplay is a deterministic, two-dimensional, decentralized search-and-destroy game. One submission supplies the code independently instantiated for each of eight identical units. There is no team-level controller at runtime.

Implement only this gameplay mode. Do not add extraction, capture points, shrinking zones, commanders, character classes, respawning, reviving, grenades, suppression, sound simulation, destructible terrain, machine-learning training, or a large web application. Simple rush controllers are sufficient baselines. “Same tournament format as V2” means preserving the competition workflow and rating/publication behavior, not preserving V2's vehicle classes, goal scoring, physics, controller API, or old ratings.

Python 3.12 is a suitable starting point. Prefer a small dependency set; keep the authoritative engine independent of graphics and FFmpeg. Pillow plus FFmpeg is sufficient for rendering. The Python import/CLI package may remain `swarmbench` within the independent V3 project. Start engine, API, generator, replay, and tournament schema versions at V3 values; do not silently interpret V2 data as V3 data.

Before implementation, write a short staged checklist and keep it updated. Make reasonable choices for unspecified low-level details, document them, and continue without asking me to resolve routine implementation questions. Do not push changes, enable repository settings, publish releases, or trigger remote tournaments without separate authorization; implement and locally test those capabilities.

## 2. Fixed initial rules

Centralize these values in a typed rules/configuration object shared by the engine, API documentation, replay metadata, tests, and renderer. These are prototype balance defaults, not a claim of validated game balance. Official matches use one versioned ruleset, not arbitrary participant-supplied settings.

| Parameter | Initial value |
| --- | --- |
| Teams | Two |
| Units | Exactly eight identical units per team |
| Match duration | 120 simulation seconds maximum |
| Arena | 120 m in x by 80 m in y |
| Terrain | Initially unknown, static 1 m occupancy cells |
| Unit shape | Disc, radius 0.4 m |
| Maximum speed | 4 m/s |
| Maximum acceleration/deceleration | 8 m/s², norm-limited; no jerk model |
| Physics frequency | 20 Hz |
| Controller frequency | 10 Hz |
| Main visual detection range | 18 m, center-to-center |
| Main field of view | 120° cone centered on actual facing |
| Close awareness | 3 m all-around, still blocked by walls |
| View and gun heading | Coupled; independent of movement direction |
| Maximum heading rotation | 180°/s |
| Weapon | Deterministic hitscan ray along actual gun heading |
| Weapon range | 22 m |
| Health | 100 HP |
| Damage | 20 HP per hit |
| Fire interval | 0.8 s |
| Magazine | Six rounds, initially full |
| Reload duration | 2.0 s |
| Reserve ammunition | Unlimited |
| Radio | One optional unsigned 64-bit payload per unit per control step |
| Radio range | 30 m, center-to-center |
| Radio latency | Exactly one control step, 0.1 s |
| Radio propagation | Reliable friendly-only broadcast, through walls |
| Unit movement collisions | Enabled for both friendly and opposing units |
| Friendly fire | Enabled; same damage as enemy fire |

Use global Cartesian coordinates: origin at the lower-left, +x right, +y up, distances in meters, angles in radians counterclockwise from +x. Units know the map dimensions and coordinate conventions, but not undiscovered terrain.

Winning: eliminate all opposing units to win immediately. Resolve a simultaneous volley completely before checking this condition; simultaneous elimination of both teams is a draw. At the 120-second boundary, more surviving units wins; equal survivor counts is a draw, regardless of remaining HP or damage dealt. No exploration, communication, damage, or territory points. A hard controller failure can cause a forfeit under the runtime rules below.

## 3. Movement, combat, and simulation order

### Movement

Controllers request a desired global velocity and desired global facing angle. Clamp the velocity norm to the speed limit, then approach it under the acceleration/deceleration limit. Movement direction and facing are independent, so moving sideways/backward is legal. Rotate toward the requested facing along the shortest angular path under the rotation limit. Specify the exact 180° tie convention.

Units cannot overlap each other or walls. They cannot pass through each other, exchange positions through one another, or tunnel through walls between integration samples. Contacts block/slide movement; they do not damage or eliminate units. Use deterministic swept collision handling or another tested conservative method, with a documented numerical tolerance. Avoid systematic team or iteration-order priority. Resolve multi-unit contacts consistently; do not use random nudges or teleports to escape congestion. Corpses immediately stop participating in physics, sensing, radio, and shot interception, although replay visuals may show a brief death marker.

### Weapons

A shot is a ray from the shooter's center along its **actual** gun heading. Ignore only the shooter. The first wall or other living unit hit stops the ray. Walls win exact-distance ties. Both friendly and opposing units intercept shots and receive 20 damage. Shots do not pierce. Use the same physical disc radius for unit hits; do not silently give bullets an unrelated oversized hit radius.

There is no random spread, critical damage, aim assist, projectile travel, or movement accuracy penalty in this first version. A unit may fire at an unseen location: the action is not restricted to a currently visible enemy ID. A radio report can therefore help choose a firing direction, but cannot bypass walls or physical interception.

A valid fire request attempts at most one shot that control step, subject to cooldown, ammunition, and reload state. Fire requests are transient, not queued or retained. Explicit reload starts a full two-second reload, replacing the magazine with six rounds on completion. A reload on a full magazine is a no-op; repeated requests during a reload do not restart it. An accepted reload request takes precedence over firing. Automatically start reloading immediately after the last round is fired. Allow movement, turning, sensing, and radio while reloading.

Compute all shots in a control tick from the same pre-damage snapshot, then apply damage together. A unit hit lethally in that volley can still execute its already-valid shot. Units alive before the volley can intercept multiple rays from that volley; there is no order-dependent opening of a path through a just-killed unit.

### Exact control-step order

Use integer tick counters for scheduling; derive display time from them. At a control boundary `t`:

1. Complete timers due at `t`; provide each living unit with its observation of the same world snapshot and its queued inbox.
2. Collect controller actions with simulation time paused. No controller sees another unit's current-step action or response timing through the game API.
3. Validate actions. Record radio recipient eligibility from the positions and living units in this boundary snapshot. Start valid reload requests and evaluate valid fire requests using the **current** physical positions and headings. A newly requested heading does not instantly rotate the gun before shooting.
4. Resolve the volley simultaneously, apply damage, remove eliminated units, and test elimination termination.
5. If play continues, integrate movement and heading for two 0.05-second physics substeps, reaching `t + 0.1`. Queued messages become eligible for the next observation.

A sender alive in the boundary snapshot can send its current packet even if it is eliminated in that boundary's volley. Deliver it next step only to eligible recipients still alive then. Messages already transmitted are not retroactively cancelled.

At exactly 120.0 seconds stop and adjudicate; do not request another set of actions or fire an extra volley. Log accepted actions and authoritative events sufficiently precisely to reproduce these rules without rerunning controller code.

## 4. Unknown map generation and visibility

Generate varied connected maps containing rooms, corridors, courtyards/open areas, cover blocks, junctions, loops, and alternative routes. Avoid an empty shooting field, a single mandatory chokepoint, or an almost entirely dead-end maze. Keep walls static and opaque. A 1 m occupancy grid is the source of truth; continuous unit movement takes place over this grid.

Use openings at least 2 m wide and generally wider corridors/rooms so units can pass and choose formations. Give each major spawn region more than one useful route toward the rest of the arena. Spawn teams in separated regions toward opposing x ends, with randomized local layouts and y placement. Ensure eight non-overlapping valid spawn discs per side and no immediate initial shooting-range encounter. Do not make exact opponent spawn coordinates public.

Validate connected traversable space and spawn reachability with unit clearance accounted for, not just point-agent connectivity. Bound generator retries; provide a deterministic valid fallback or an explicit generation error rather than an infinite loop. Include a multi-seed generation test. Perfect map symmetry is not required or promised: tournament side swaps exchange controllers between the same two spawn configurations on the same generated map.

For sensing, a unit center must be within the main cone/range or close-awareness radius, and have wall-clear line of sight. Close awareness does not see through walls. Apply the same rules to friendlies and enemies. Unit bodies block bullets and movement but do not occlude vision in this initial version.

Expose current visible terrain cells in global grid coordinates, including observed free cells and the first blocking wall cells. Do not reveal a whole room, rectangle, wall object, or chunk because only one part was observed. Define cell sampling, wall visibility, diagonal corner occlusion, and boundary inclusivity explicitly. Share trusted visibility/geometry logic between observation generation and replay perspective rendering. Avoid visibility through zero-width diagonal cracks.

Return only current observations. Each controller stores its own explored map and sighting history. Do not supply an automatically maintained shared team map or enemy last-seen database. Optimize local visibility enough for sixteen units and 1,200 control steps per full match, without introducing a heavyweight geometry framework unnecessarily.

## 5. Per-unit API and information boundary

A submission is one regular Python file defining `UnitController(BaseUnitController)` with `initialize(info)` and `step(observation)`. Instantiate the same code independently eight times per team. Each instance controls exactly one unit and has persistent memory for that match only. Stable team-local IDs 0–7 are allowed for prearranged roles.

Use a small typed, immutable public API. Its intended shape is:

```python
class UnitController(BaseUnitController):
    def initialize(self, info: UnitInfo) -> None:
        self.unit_id = info.unit_id
        # Only this instance's private state lives here.

    def step(self, observation: Observation) -> Action:
        return Action(
            desired_velocity=(0.0, 0.0),
            desired_heading=observation.self_state.heading,
            fire=False,
            reload=False,
            broadcast=None,
        )
```

`UnitInfo` contains public rules, dimensions, initial team size, own team/unit identity, own starting state, and an independent per-unit controller RNG seed. It must not contain the hidden map, full spawn list, opponent controller name/code, master scenario seed, or a reversible derivative of that seed.

`Observation` contains the control tick/time, accurate own global position/velocity/heading/HP/ammunition/reload/cooldown state, currently visible friendly/enemy units, currently visible terrain, and received radio messages. Visible units have stable IDs, friend/enemy identity, exact current global position, velocity, and physical heading. Other units' HP, ammunition, and reload timers are not automatically exposed; those are private state. The fixed initial team sizes are public, but current remote survivor counts are not.

Do not expose a global kill feed, unseen deaths, remote friendly positions, global current scores/survivor counts, full maps, private scenario state, or omniscient hit confirmations to controllers. Own health changes and own weapon state are known. Do not attach an unseen shooter's identity/position to a damage notification. Local perception and radio are how units obtain other information.

Validate finite vectors, norms, angle normalization, booleans, and integer ranges. Missing movement/heading retains the previous desired setpoint; missing fire/reload/broadcast means false/false/silence. Malformed fields are rejected and counted with a documented deterministic fallback. Reject Boolean values masquerading as message integers, negative messages, and values above `2**64 - 1`. Do not truncate/wrap oversized messages. Unknown action fields cannot smuggle extra broadcasts.

Official runtime must not expose hidden state through filesystem mounts, environment, inherited process memory, replay files, tournament plans, or public IDs. Keep the authoritative scenario RNG separate from controller RNGs. Full scenario state can appear in post-match replays; avoid reusing disclosed seeds as unseen evaluation scenarios.

## 6. Radio semantics

Each living unit may send `None` or exactly one unsigned 64-bit payload at each 10 Hz control boundary. A zero payload is a real message; it is not silence. This is an eight-byte payload limit, not a promise that all transport metadata fits in eight bytes.

Every other friendly living unit at distance <= 30 m in the transmission boundary snapshot is eligible. Walls do not attenuate/block radio. Reception is reliable and occurs in the next observation, provided the receiver remains alive. Movement into or out of range during the interval does not change this already-decided recipient set. No self-reception, opponent eavesdropping, signal-strength reports, packet loss, channel contention, automatic forwarding, or multi-hop delivery.

Receivers may obtain up to seven messages per step. Each contains only the authenticated immediate sender's team-local ID and the payload. Sort messages deterministically by sender ID. The engine does not add sender coordinates, original observer identity, observation timestamp, hop count, interpretation, or acknowledgements. Receivers know the delivery tick from their observation; relayed information's age must be encoded by the protocol. A relay consumes the relay's own one-packet budget on a later step.

No sender gets an omniscient list of recipients. Log transmissions/deliveries for trusted replay/debugging, not for controllers. Visible movement may naturally convey information; the radio restriction prohibits extra programmatic shared-state channels, not ordinary in-game signaling.

## 7. Independent execution, security, and runtime cost

Separate controller objects in one Python interpreter are insufficient. Official execution needs one independently isolated worker/container per unit: sixteen for a full match. A team-shared container or shared writable volume is not an acceptable communication boundary.

Adapt V2's persistent Docker runner so workers persist across steps but are reset completely between matches. Each unit gets private process/IPC/network namespaces and private scratch space, with no shared writable files, sockets, `/dev/shm`, host process namespace, Docker socket, credentials, opponent code, or hidden engine state. Use non-root execution, read-only code/root filesystem, disabled network, dropped capabilities, no-new-privileges, and bounded CPU/memory/process/log/output resources. Never use pickle or another code-executing deserializer across the untrusted boundary.

A local fresh-process backend can exist for trusted development, but label it explicitly as not a hostile-code security sandbox. Do not fork controller workers out of an engine process containing the hidden world; use fresh exec/spawn or containers. Static source checks help usability but are not the security boundary. Document residual OS/timing side-channel limitations honestly rather than claiming formal isolation.

Starting runtime limits: 10 s initialization watchdog, 100 ms soft per-step wall deadline, 1 s hard deadline, 256 MiB memory and 16 MiB private scratch per unit, and one numerical-library thread. Apply CPU/process quotas and bounded protocol/log sizes as well. Collect all live-unit responses concurrently from the same observation boundary; do not accidentally wait sixteen full timeout windows serially. Budget and measure the complete sixteen-worker match, not just one controller.

A soft miss discards that entire unit action, including fire and radio; previous movement/heading persists and private controller memory is not rolled back. Tag requests/replies with ticks so stale replies cannot become future actions. A hard timeout, uncaught controller exception, or broken protocol causes that submission's team to forfeit. If both submissions have controller-caused hard failures at the same barrier, record a double-forfeit draw, not a win for whichever team was processed first. Distinguish a trusted infrastructure failure from a controller forfeit: infrastructure failures must not silently become rated losses.

These resource defaults may be adjusted before freezing V3 if measurement on the target runner demonstrates a problem. Document any adjustment and its evidence. Do not silently remove isolation to make CI faster. Keep dependency choices and resource limits consistent between validation and rated play; do not preload heavyweight ML frameworks in every worker for these simple baselines.

## 8. Simple baselines and submission template

Implement three small, understandable baselines: `rush`, `spread_rush`, and `radio_rush`. They are examples, not an advanced strategy project.

`rush` moves generally toward the opposite side, handles newly seen walls with simple local mapping/navigation, turns toward visible enemies, fires when aligned, and reloads. `spread_rush` varies its route using its own unit ID to avoid immediately bunching all units into one line. `radio_rush` additionally broadcasts a recent contact and uses received contact reports to select a destination/facing. All must avoid knowingly firing through visible friendlies where practical, handle congestion, and recover from ordinary blocked routes. No access to authoritative maps or enemy state is allowed even for built-ins in normal matches.

A suitable example contact packet is little-endian `<BBHHH>`: one-byte type, one-byte enemy ID, uint16 x in centimeters, uint16 y in centimeters, and uint16 original observation control tick. This is exactly eight bytes and covers the arena and match duration. Document it as the baseline's optional protocol, not an engine-mandated message schema. Include pack/unpack tests and ignore stale or malformed reports. Received coordinates do not guarantee an unobstructed firing line.

Provide a minimal standalone submission template and adapt the one-file submission convention to `submissions/<github-login>/<controller-name>.py`. One uploaded file supplies the entire team through independent instances. Do not port V2's advanced combat controllers, learned weights, or community submissions.

## 9. Preserve V2's tournament and submission workflow

Port/adapt the existing competition system, including its Glicko-2 implementation/tests, pairing logic, small/default/large presets, official/exhibition distinction, frozen schedule, deterministic side-swapped scenarios, provisional calibration, trusted reports, and atomic publication. Inspect and preserve the actual local V2 behavior unless it conflicts with an explicit V3 requirement.

In particular, preserve the six-hourly schedule at minute 17, manual workflow dispatch, default approximately eight opponents with mostly nearby-rating opponents plus exploration, and default four scenarios with side swaps (eight games per pairing). Keep initial Glicko-2 values 1500 rating, 350 RD, and 0.06 volatility. Wins/draws/losses, not damage or survivor difference, update ratings. Start with a fresh V3 leaderboard containing only appropriate V3 baselines; do not carry over V2 ratings/history.

Use one match at a time within each compute runner and parallelize independent frozen batches as V2 does, retaining its configurable up-to-19-batch parallelism. The extra per-unit workers are an internal match execution change, not sixteen additional tournament entries. Freeze engine/rules versions, controller hashes, source revision, pairings, scenarios, and sides. Fresh workers/memory are required for each side-swapped game. Do not leak hidden scenario seeds into controller inputs or pre-match reports.

Preserve V2's security separation: untrusted compute/validation has read-only repository permissions and no publishing secrets; trusted reporters/publishers never import submission code or check out an untrusted PR head. Bind artifacts to expected run, revision, controller hash, path, batch, and schedule. Validate primitive types, counts, ranges, uniqueness, versions, decompression sizes, and paths. A missing, duplicate, inconsistent, or invalid batch fails the period closed without updating ratings. Verify artifact integrity as well as schema validity.

Preserve permanent Tournament Results Discussions, periodic progress replies, official current-rating/README bot PRs, and serialization of rating publication with accepted-submission publication. Do not overwrite newly accepted controllers or overwrite concurrent rating updates. Exhibition runs never alter ratings. Keep latest state in Git and historical reports in Discussions rather than committing tournament history and media every period.

Port the corresponding V2 test, submission-validation, submission-reporter, submission-accepted, and tournament workflows. Retain a clear required `Submission Gate` and SHA-bound provisional calibration. Replace V2's now-irrelevant empty-arena goal-scoring smoke test with V3 API, runtime, deterministic combat/movement/radio, and baseline match smoke tests. Do not require arbitrary win rates, exploration amounts, or aggressive behavior just to accept a legal controller. Simple legal defensive controllers can draw.

Document the repository setup that code cannot automatically provide: Discussions/category, required check, auto-merge/squash policy, GitHub App installation, variables/secrets and permissions. Use V3-specific concurrency/image identifiers where needed and derive the current repository dynamically. Do not leave hardcoded V2 publishing destinations. Missing remote credentials should not prevent local tests or exhibition matches; clearly report untested remote publication rather than pretending it succeeded.

## 10. Replay and graphical rendering pipeline

Keep the authoritative simulator headless. Rendering is a replay consumer and must not affect match timing, controller budgets, outcomes, or rating publication.

### Authoritative replay

Use versioned, validated compressed JSON (`.json.gz`) containing the full generated scenario, rules/version/source identity, controller hashes, accepted actions, shot endpoints/hits, damage/deaths, reload events, radio transmissions/deliveries, timing/forfeit events, final result, and enough movement/timing information for exact trusted reconstruction. Use periodic checkpoints only where they provide real benefit. Reconstruct with the trusted engine and recorded accepted actions, never by re-executing submissions.

Reuse observation/visibility logic to reconstruct unit perspectives, and test the reconstructed observations against live observations. Store small per-step observation hashes or compact visibility records where useful; avoid writing huge redundant all-cells/all-units arrays every step unnecessarily. Replay inputs and strings remain untrusted data: bound them before rendering or publishing.

### Visual presentation

Default to a clear top-down **omniscient spectator** view: readable walls/openings, distinct team colors, unit IDs/facing, health bars, reload indicators, short-lived hitscan tracers, obvious friendly-fire events, match clock, living-unit counts, controller names, and final result. Use small discs/icons, not detailed human art. Keep overlays legible without excessive clutter.

Also support `--perspective unit --team A --unit-id 3`: show only that unit's directly observed terrain history, current visibility, currently visible other units, its own state, and its actual raw received radio payloads/sender IDs. Unseen moving units must not continue moving on this view; optional last-seen ghosts must be explicitly labeled and frozen at the last observation. Do not show global survivor counts or global kill feeds in this perspective. If the selected unit dies, show its local termination without revealing the subsequent hidden match state.

This is a visualization of the unit's sensor history, **not its internal controller belief**. Arbitrary 64-bit payloads cannot automatically update a rendered semantic map; do not pretend to decode arbitrary protocols. An optional team-union view is spectator diagnostics only and must say that no controller actually receives that aggregate view.

Add optional FOV/visibility and radio overlays. In spectator mode, radio pulses/links can show the actual delivery graph; do not display undisclosed receiver positions in unit perspective. Reuse cached static backgrounds and stream frames rather than keeping an entire match's uncompressed frames in RAM.

### MP4-first output

Make `.mp4` the default video output. Starting quality: 20 FPS, a 1200×800 arena viewport plus an 80-pixel HUD (1200×880 total), preserving the physical 3:2 map aspect ratio. Offer configurable resolution, FPS, playback speed, clip start/duration, and perspective without changing authoritative simulation.

Stream frames to FFmpeg. Use H.264/libx264, `yuv420p`, CRF 23, a reasonable speed preset such as `fast`, no audio, and `-movflags +faststart`. Keep output dimensions even, handle encoder errors/stderr without deadlocks, and write atomically/clean up partial outputs. Explicitly requesting MP4 when FFmpeg/libx264 is unavailable must produce an actionable error, not silently create a GIF or mislabeled file. Headless matches and replay generation must still work without rendering dependencies.

Keep GIF support **explicit and limited to README previews**. Add a command to generate a short 10–15-second highlight, approximately 600 pixels wide at 10 FPS, using a bounded palette. Do not create full-match GIFs in normal match/tournament runs. Generate a PNG poster/thumbnail for each published MP4. A README GIF may link to its corresponding published full MP4/release page; do not invent an upload URL before publication actually succeeds.

### Tournament media publication

Preserve raw match computation as V2 does. Select at most three representative replays per completed tournament with a documented deterministic rule and stable tie-breaks, rather than rendering every game. Produce a versioned media manifest binding each selected replay/video to the validated match and content hashes.

Run selected-match rendering in a separate credential-free job using trusted renderer code. A render/upload failure must be reported as a media problem, not invalidate a successfully computed/rated tournament or hold the rating-publication lock. Rating finalization must not depend on successful video generation.

Upload MP4s, PNGs, compressed selected replays, and the manifest as Actions artifacts with explicit retention. For durable public access, implement a separate trusted media publisher for successful official runs: upload the selected files as Release assets, grouped under a media release such as `tournament-media-YYYY-MM`, with unique run/match-prefixed filenames. Media releases must not become the latest software release. Check the repository's release-immutability policy; if existing releases cannot accept more assets, use separate per-run media releases instead of attempting to mutate an immutable release. Do not commit MP4s or large replay histories to Git or move existing release tags on each run. Make publication idempotent and handle concurrent release creation safely.

Append actual asset links to the tournament Discussion from a trusted publishing/reporting stage. Use returned asset URLs rather than constructing assumed links. Treat these as ordinary video/download links; do not depend on GitHub automatically turning arbitrary MP4 links into inline video players. If remote media publishing is not configured, keep downloadable artifacts and state that limitation explicitly; Actions artifacts are not permanent public video hosting.

Keep the README GIF as a manually selected showcase, not an automatically changing binary in every rating PR. A manual showcase command/workflow may create the preview and a reviewable README/media change. The normal ranking bot PR should remain a small current-state update. Do not introduce a database, external storage account, or frontend framework for this first version.

## 11. CLI, docs, and acceptance tests

Provide working commands equivalent to the following; adapt names consistently if necessary:

```bash
python -m pip install -e '.[dev,competition,render]'
python -m pytest
python -m swarmbench arena --seed 42 --render arena.png
python -m swarmbench validate submissions/example/controller.py
python -m swarmbench match --controller-a rush --controller-b radio_rush \
  --seed 42 --replay out/match.json.gz
python -m swarmbench render out/match.json.gz --output out/match.mp4
python -m swarmbench render out/match.json.gz --output out/unit-A3.mp4 \
  --perspective unit --team A --unit-id 3
python -m swarmbench render out/match.json.gz --output out/preview.gif \
  --readme-preview --start 20 --duration 12
python -m swarmbench tournament --mode exhibition --size small --seed 42
```

Document the recommended Docker backend and clearly label any local unsandboxed default. Document controls, observations, coordinate frames, radio packet examples, exact tick ordering, collision/shot geometry, runtime limits, determinism caveats, submission/CI flow, repository setup, media publication, and how to inspect a unit's perspective. Include a short V2-to-V3 migration note and a concise `AGENTS.md` for future contributors. Keep documentation grounded in implemented behavior.

At minimum, test:

- Identical rules for all sixteen units, exact match duration, elimination, survivor-count time-limit wins, equal-survivor draws regardless of HP, and simultaneous mutual elimination.
- Velocity/acceleration/turn limits; wall and friendly/enemy unit collision; no overlap, tunneling, swap-through, or order-dependent death-on-contact behavior; congestion and doorway cases.
- FOV/range boundaries, close-awareness wall occlusion, correct global coordinates, visible-wall limits, no full-map/remote-unit leakage, and map memory remaining controller-local.
- Nearest hitscan interception, wall shielding, friendly damage, aiming at unobserved coordinates, rotation before subsequent firing, fire interval, magazine depletion/reload, and simultaneous volley results.
- Radio 0/max payloads, invalid payloads, exact range boundary, through-wall delivery, one-tick latency, recipient movement, sender/receiver death timing, no self/opponent reception, and no automatic same-tick/multi-hop forwarding.
- Private per-unit module/global/file state in the official backend; no extra IPC/shared-map channel; timeout and exception handling, stale responses, bounded output, and complete resets between matches.
- Multi-seed map validity/reachability and byte-for-byte or canonical-state deterministic replay for the same recorded accepted actions and engine version. Separate controller/OS timing nondeterminism from engine determinism.
- Fresh V3 ratings, side-swapped schedules, simultaneous Glicko updates, exhibition immutability, SHA-bound calibration, incomplete/tampered batch rejection, and safe publication concurrency.
- MP4 smoke output verified with FFprobe when available, explicit missing-encoder failure, valid GIF preview, correct aspect ratio, no information leakage in unit view, and media failure not altering ratings.

Run a practical sample of full baseline matches over multiple seeds and side swaps, including the sixteen-worker backend when available. Record wall time, per-unit latency, memory, first-contact time, no-contact/draw rate, friendly-fire count, and radio send/receive counts. These are diagnostics, not scoring bonuses or arbitrary submission-gate thresholds. Do not claim communication is useful merely because packets are sent; a simple radio-on/off comparison may be reported without asserting that it proves the game's balance.

## 12. Delivery and work order

Implement in this order: inspect/port the reusable competition foundation; define the V3 rules/API; implement and test map/physics/visibility/combat/radio; implement isolated workers and simple baselines; integrate validation/tournaments; finish replay/MP4/unit-view/media workflows; then run the acceptance suite and write exact setup/run instructions.

At completion, summarize what is implemented, important design choices, tests and smoke matches actually run, measured runtime, generated demo artifacts, remaining issues, and remote repository settings still needed. Clearly distinguish implemented-but-not-live-tested GitHub publishing from verified local functionality. Do not present empty stubs, skipped tests, missing FFmpeg/Docker, or unconfigured remote services as completed validation. Where a dependency is unavailable, implement and test the remaining work, document the exact blocker, and leave reproducible commands for verification.

The goal is a usable V3 repository with honest validation, not a large collection of optional systems or an optimized tournament-winning controller.
