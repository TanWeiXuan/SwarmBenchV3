# SwarmBenchV3 game specification

`swarmbench.rules.OFFICIAL_RULES` is the typed source of truth. Engine, public API, replay metadata, renderer and tests consume the same immutable object. The initial identity is engine `3.0.0`, controller API 3, generator 3, replay 3, tournament 3 and ruleset `v3-prototype-1`.

## Coordinates and schedule

The origin is the lower-left. +x points right, +y points up, distances are metres, and angles are radians counterclockwise from +x. A match has exactly 1,200 control boundaries, ticks 0 through 1,199. Display time is `tick / 10`; scheduling never compares floating-point time.

At boundary `t` the engine:

1. completes reloads due at the integer tick and constructs every living unit's observation from one snapshot and its already-eligible inbox;
2. collects all live-unit responses concurrently while simulation time is paused;
3. validates complete actions, snapshots radio eligibility, accepts reloads, and evaluates fire along the current physical heading;
4. resolves every accepted shot against the same pre-damage snapshot, applies combined damage, removes dead units, and checks elimination;
5. if both teams remain, integrates two 0.05 s movement/heading substeps and advances one control tick.

No action is requested at 120.0 s. A sender alive in step 3 transmits even if killed in step 4. An eligible receiver gets that packet next tick only if it remains alive.

## Movement and contacts

Desired velocity is globally oriented, norm-clamped to 4 m/s, and approached by at most 8 m/s². Facing is independent and approaches the requested normalized angle at at most π rad/s. For an exact 180° shortest-turn tie, rotation is counterclockwise.

Walls are unit occupancy-cell AABBs. Each 0.05 s substep first performs conservative disc/AABB movement with deterministic axis sliding. At maximum speed a unit travels only 0.2 m per substep. Unit/unit collisions use relative swept distance over the substep; every member of a colliding set has that substep movement blocked simultaneously. This symmetric conservative response prevents overlap, tunnelling and exchange-through without team/iteration priority. Contacts do no damage. The numerical comparison tolerance is `1e-9`. Corpses immediately leave physics, sensing, radio and shot interception.

## Vision and unknown terrain

Unit centres are visible at distance <= 18 m and angular offset <= 60°, or at distance <= 3 m in any direction, provided the shared supercover grid trace has no wall. Bodies do not occlude vision. Boundary comparisons are inclusive.

Visible terrain is sampled by 81 evenly spaced rays across the inclusive 120° cone and 48 evenly spaced all-around close-awareness rays. Traversed free cells and the first wall cell are returned in global integer cell coordinates. The supercover traversal includes both side cells at an exact diagonal corner, so a zero-width diagonal crack is opaque. No room/chunk expansion or remembered map is supplied. Each controller instance may maintain only its own history.

Generation is seeded and bounded to 24 attempts. Porous dividers, staggered side cover and partial room walls create loops, junctions, rooms/courtyards and multiple cross-arena routes. Openings are five cells wide. Validation uses four-neighbour connectivity of free cell centres; their 0.5 m wall clearance exceeds the 0.4 m unit radius. All sixteen spawn cells share one clearance-valid component, spawn discs do not overlap, and opposing spawns are outside initial weapon range. Failure after the retry bound raises `GenerationError`.

## Hitscan and reloads

A shot starts at the shooter's centre on its actual pre-action heading. Exact ray/disc and ray/cell-AABB intersections choose the nearest living unit or wall out to 22 m. Walls win distance ties. Only the shooter is ignored; friendly units intercept and take the same 20 damage. Shots never pierce.

One transient fire request can be accepted per control step. The interval is eight control ticks. A non-full explicit reload starts a 20-tick reload and takes precedence over fire. Reload on a full magazine is a no-op, so a simultaneous legal fire request can still fire. Repeated reload requests do not restart an active reload. Firing the sixth round immediately starts automatic reload. Movement, turning, sensing and radio continue.

The volley is simultaneous: a lethally hit unit can execute its already accepted shot, and a unit alive before the volley intercepts every applicable ray in that volley.

## Radio and result

A living unit may emit `None` or one integer in `[0, 2**64-1]`. Boolean values are rejected. Every other friendly living centre at distance <= 30 m in the boundary snapshot becomes eligible, regardless of walls. Delivery is reliable at the next observation, sorted by immediate sender ID, with only `(sender_id, payload)`. There is no self/opponent reception, recipient list, location, signal strength, forwarding or interpretation.

Eliminating all opponents wins immediately. Simultaneous mutual elimination draws. At tick 1,200, the team with more living units wins; equal survivors draw regardless of HP, damage, exploration or communication.
