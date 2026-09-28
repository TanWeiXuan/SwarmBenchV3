# V2 to V3 migration note

The read-only local reference was `SwarmBenchV2` commit `e3d3ecf5a4dc4791a27c4c874c4650a3f26be545`.

Retained/adapted under the shared MIT license:

- Glicko-2 mathematics and simultaneous period updates;
- rating-nearby/exploration pairing, deterministic scenario derivation and side swaps;
- small/default/large presets, up-to-19 frozen batches and one match per runner;
- persistent JSON workers with concurrent side barriers, soft/hard watchdogs and fresh match reset;
- fail-closed primitive artifact validation, current-only Git state and serialized atomic bot PR publication;
- untrusted compute versus trusted reporter/publisher workflow separation and permanent Discussions.

Replaced rather than interpreted:

- omniscient team/vehicle API → immutable local per-unit API and sixteen isolated workers;
- mixed classes, goals, contact destruction and projectiles → identical health units, solid contacts and simultaneous hitscan;
- mirrored obstacle arena → unknown 1 m connected occupancy grid;
- V2 replay/generator/controller/tournament schemas → explicit version 3 schemas;
- GIF-fallback renderer → MP4-first streaming renderer plus information-bounded unit perspective.

V3 has no imports, runtime paths, ratings, history, submissions, credentials or Git metadata from the V2 checkout.
