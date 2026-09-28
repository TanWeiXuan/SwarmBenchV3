# Per-unit controller API v3

A submission defines `UnitController(BaseUnitController)` in one regular Python file. The worker imports that file independently eight times per team, once per process/container.

`initialize(info: UnitInfo)` receives immutable official rules, `(120, 80)`, own team and team-local ID 0–7, initial team size eight, only this unit's starting state, and an independent controller RNG seed. It does not receive the occupancy grid, other spawns, opponent identity, master scenario seed or a reversible encoding of it.

`step(observation: Observation)` receives:

- integer control tick and display time;
- exact own position, velocity, physical heading, HP, ammunition, reload and cooldown state;
- currently visible friendly/enemy IDs, exact kinematics and heading, but not their HP/ammunition/timers;
- currently ray-sampled visible free/wall cells;
- this tick's authenticated immediate-sender radio messages.

There is no kill feed, hit confirmation, score, survivor count, remote friendly state, last-seen database, shared map or automatic protocol decoding.

Return `Action(desired_velocity=None, desired_heading=None, fire=False, reload=False, broadcast=None)`. Missing movement/facing retains the previous setpoint; other missing fields mean no request. Velocity is finite and norm-clamped. Heading is finite and normalized. `fire` and `reload` must be actual booleans. `broadcast` must be an actual integer from 0 through `2**64-1`; it is never wrapped. Unknown fields are counted invalid and cannot carry data.

A complete response after 100 ms is discarded, including fire and radio, while previous movement/facing remains. Private controller state is not rolled back. At one second, an uncaught exception, stale tick/sequence, oversized/malformed JSON or broken worker is a team forfeit. Simultaneous controller-caused hard failures at one barrier are a double-forfeit draw. Infrastructure failures are raised separately by tournament jobs and do not become rated losses.

The `radio_rush` example optionally uses little-endian `<BBHHH>`: contact type, enemy ID, x centimetres, y centimetres and original observation tick. That is a baseline convention, not an engine protocol. Reports older than 30 ticks or outside arena bounds are ignored; coordinates never imply line of fire.
