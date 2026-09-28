# Replays, perspectives and media

`.json.gz` is canonical JSON compressed with a fixed gzip timestamp. It records the full generated grid/spawns, exact official rules and versions, source/controller identities and hashes, every validated per-unit action, physics frames, shots/endpoints/hits, damage/deaths/reloads, radio recipient/delivery metadata, failure events, per-observation hashes and final-state hash. Loaders bound compressed data to 32 MiB, expanded data to 128 MiB, sequence counts and string sizes.

`verify_reconstruction` creates a trusted fresh engine, regenerates every observation, compares its compact hash, applies recorded validated actions without executing controller code, and checks the final canonical state hash.

MP4 is the default presentation: 20 FPS, 1200×800 arena plus 80 px HUD, libx264, CRF 23, `fast`, `yuv420p`, no audio and `+faststart`. Frames stream to FFmpeg; stderr goes to a temporary file to avoid pipe deadlock; a `.part.mp4` is atomically replaced only after success. A PNG poster is generated beside every MP4. Missing FFmpeg/libx264 is an actionable error and never silently becomes GIF.

`--perspective unit --team A --unit-id 3` accumulates only directly ray-observed terrain, draws only currently visible moving units, shows own private state and raw `(sender, 64-bit payload)` reception, and stops at local death. It never displays global counts, kill feed, undisclosed radio links, moving unseen ghosts or decoded arbitrary payload beliefs. `--fov` and spectator `--radio` overlays are optional.

GIF requires `--readme-preview`, exactly 10 FPS, 10–15 seconds and width <= 650. It is a bounded manually selected README medium, not a full-match tournament output.
