# Security model

Controller files are arbitrary untrusted Python. AST/import checks improve errors; they are not the boundary.

## Official Docker backend

Every live unit has its own persistent container—sixteen per match—and every side-swapped game starts fresh containers. Containers use no network, private IPC, a read-only root/code bind, a private 16 MiB `noexec,nosuid` scratch tmpfs, UID/GID 65534, all capabilities dropped, `no-new-privileges`, one CPU, 256 MiB memory/no swap and a 64-process limit. Numerical-library thread variables are one. JSON lines are capped at 1 MiB and retained stderr at 64 KiB. The Docker socket, host PID namespace, credentials, replays, plans and hidden engine state are not mounted.

The host starts each worker with a fresh exec; it does not fork from a process containing the world. Requests and responses carry protocol version, sequence and tick. Sixteen replies are awaited concurrently. Initialization has 10 s; steps have 100 ms soft / 1 s hard wall deadlines.

This is pragmatic OS isolation, not a proof. Host scheduling and aggregate timing/cache behaviour can still create residual side channels. Docker/host-kernel vulnerabilities remain in scope. Official runners should be dedicated and patched.

## Workflow trust separation

Submission validation and tournament compute declare only `contents: read`, receive no App key, and execute controller code only in Docker. Trusted Discussion/rating/media publishers operate on merged `main`, never import a submission, and validate versioned primitive JSON bound to run, source revision, plan hash, controller hashes, game IDs, seeds, sides and artifact hashes. Any missing, duplicate, stale or inconsistent batch closes the period without ratings.

## Local backend

The default local backend gives each unit a fresh process and private temporary current directory, which tests per-unit state semantics. It is explicitly **not safe for hostile code**: the process can access files and credentials available to the user. Use Docker or a disposable VM for unknown submissions.
