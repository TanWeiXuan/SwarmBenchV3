# Contributor instructions

- Treat the engine as authoritative and deterministic; rendering must remain a replay consumer.
- Preserve the per-unit information boundary. Never add team-shared controller state or hidden-world fields to the public API.
- Use integer ticks for simulation scheduling and keep official rules in `swarmbench.rules.OFFICIAL_RULES`.
- Keep V2 a read-only reference. V3 must never import from or depend on a V2 checkout.
- Run `python -m pytest` plus at least one local baseline match after engine, API, worker, replay, or tournament changes.
- Do not weaken Docker isolation, artifact validation, workflow permissions, or trusted/untrusted job separation to make tests easier.
