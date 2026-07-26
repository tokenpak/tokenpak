---
"tokenpak": minor
---

Let `tokenpak codex` run parallel sessions, the way native `codex` already does.

Codex keeps its local state in write-ahead-logging SQLite databases, which
coordinate many concurrent readers and a serialized writer across processes.
Concurrent sessions against one Codex home are therefore a supported, normal
operation. The launcher never opens those databases, so it has no stake in
their lock state and nothing to gate a launch on.

The launcher previously ran a kernel-level holder inspection before every
launch and refused to start when it saw contention. That was wrong in premise
and unreliable in practice:

- A held shared-range or dead-man-switch lock is the steady state of a healthy
  write-ahead-logging connection, not evidence of a conflict, so ordinary
  concurrent use was reported as contention.
- The inspection walked every process on the machine and failed closed when it
  could not read `/proc/<pid>/fd` for any same-user process. Setuid and
  non-dumpable daemons that never open a Codex database at all — `gpg-agent`,
  `tailscaled`, `fusermount3` and others — marked the scan incomplete and
  blocked the launch. On an affected machine `tokenpak codex` could not start
  at all, and no environment variable bypassed it.
- The refusal message claimed "SQLite lock evidence" on a code path reached
  precisely when no lock evidence and no holder had been found.

Launch is no longer gated. Holder inspection is retained only where it guards
a genuinely destructive operation — uninstalling a Codex home and reclaiming an
isolated one — because deleting files under a live session loses data. That
remaining check no longer treats a readable non-Codex process name as unknown,
so unrelated non-dumpable daemons can no longer block an uninstall either. A
real Codex session is same-user and dumpable, so the descriptor scan still
observes it directly.

The `codex.pid` lifecycle lease was a second, independent gate on the same
path. It is a single slot per home, and claiming it exclusively refused a
concurrent shared session with "already claimed by PID N" well after the
database inspection had passed, so removing the inspection alone did not
actually permit parallel sessions. Owning that slot exclusively is correct for
a home TokenPak generated and may later reclaim — retention must never delete a
directory a live session is using — but the user's own shared home is neither
generated nor ever deleted by TokenPak, and Codex coordinates concurrent
sessions on it. A shared-home launch that finds the slot held by a live session
now proceeds without owning it rather than refusing, and every lease mutation
is a no-op for a non-owner so it can never disturb the owner's sentinel.
Generated homes are unchanged and still refuse a second claim.

Deterministic per-project (`workspace`) homes also keep exclusive claims: they
are TokenPak-generated and provisioned, so admitting concurrent sessions there
would race two provisioning passes over one home. Running several sessions in
one project still requires `shared` (the default) or `isolated`.

Removed: the launcher's preflight result types, along with the mechanism that
produced them — `PreflightStatus`, `PreflightEvidence`, `FallbackDecision`,
`PreflightEvaluation`, and `TemporarySessionChoice`. They were published as
additive beta API in v1.14.0 and described a launch gate that no longer exists;
they were never usable independently of it. The interactive "start a temporary
session without the prior shared history" recovery prompt is removed for the
same reason — there is no longer a block for it to recover from. Receipts no
longer carry the `codex_preflight` or temporary-recovery members.

These symbols were importable, but importing them was never required to use
TokenPak: the CLI, the proxy, the companion, and the launchers are the supported
surfaces, and none of them oblige a caller to reference launcher internals.
TokenPak's importable API is strictly optional and never mandatory — nothing
TokenPak ships requires you to build against it — so retiring a beta symbol set
whose only purpose was to describe a removed mechanism does not break a
supported integration path. Anything that must keep working goes through the
command-line and configuration surfaces, which are unchanged here.
