# Changelog

All notable changes to TokenPak are documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

_Nothing yet._

## [1.17.0] — 2026-07-31

This minor release adds opt-in update notifications and an explicit lean
companion-output mode. Standard host output remains the default. It also fixes
several first-run, configuration, uninstall, proxy, and container-guidance
paths found by exercising behavior at its user-visible boundary.

### Added

- **Update notifications are available by explicit consent.** Interactive users
  can opt in to a bodyless package-index metadata check, cached for 24 hours, and
  choose Update or Skip when a newer release is available. Declining or pressing
  Enter sends no request. Automatic prompts stay suppressed for machine-readable,
  CI, non-interactive, server, browser-launching, and long-running commands, and
  no-network controls remain available. Explicit `tokenpak update --check` checks
  once without enabling future automatic checks.
- **Lean companion output is an explicit opt-in.** Set
  `TOKENPAK_COMPANION_STYLE=lean` to add the shared dense-technical-output
  directive to Claude and Codex companion launches. Unset and unknown values use
  `standard`, preserving each host's native output style. Install, reinstall,
  style switching, and uninstall keep the managed section bounded.

### Fixed

- Capsule compression never inflates a context block. When the capsule
  envelope would cost more than paragraph compression gains on a
  structure-heavy block (headings, bullets, and code fences pass through
  the deterministic compressor verbatim), the block is left untouched, so
  a capsulized block is always smaller than the original and measured
  savings are never negative.
- Vault matches now reach byte-preserved provider requests. Injection text is
  carried through the pipeline and inserted into the existing system array
  without reserializing the rest of the request body; the public three-value
  injection API is unchanged.
- `tokenpak start --port PORT` now honors the declared flag, with precedence
  `--port` → `TOKENPAK_PORT` → `8766`.
- `tokenpak integrate` no longer treats end-of-input as consent, and documented
  non-interactive controls suppress the prompt instead of writing client config.
- The uninstall chooser now accurately distinguishes reversible un-routing from
  stored-state removal, names the retained session journal, budget history, and
  Paks, and cancels on unknown input rather than selecting the more destructive
  mode.
- `TOKENPAK_HOME` now contains setup writes and PID cleanup within the configured
  home, including symlink-safe target checks. Corrupt config is reported as a
  distinct state, and preview error JSON uses the documented null-valued shape.
- `tokenpak preview` now refuses a bare filesystem path instead of measuring the
  path string, reports missing files without a traceback, and rejects malformed
  JSON passed with `--file`. `tokenpak config validate` returns a failure status
  when the requested config cannot be read or validated.
- Companion session labels now select a form that fits the terminal width instead
  of wrapping the host header. Their colors come from one palette shared by the
  launcher and session-start hook.

### Documentation

- Installation guidance now uses `uv tool` or `pipx` for isolated CLI installs,
  `uv pip` inside a uv-created environment, and safe PEP 668 guidance instead of
  suggesting a system-package override.
- Docker and Compose examples now agree on the mounted custom-config path and its
  matching `TOKENPAK_CONFIG` value, including short and long option forms.

### Compatibility

- No breaking change or deprecation is intended. Standard companion output is
  preserved unless lean mode is explicitly selected.
- The public API snapshot reports one additive name,
  `tokenpak.companion.launcher.Color`, and no removals. **Keep:** the additive
  launcher re-export remains available in v1.17.0; existing public names and
  signatures are unchanged.

## [1.16.0] — 2026-07-26

> Two **BREAKING** changes — the `crewai` extra is removed and the license tier ladder drops its
> two top rungs — alongside a large pass over what the CLI reports. Surfaces that printed
> constructed numbers now print a measurement or say none was taken.
>
> **On the version number.** Eleven public symbols are removed, which by strict SemVer calls for a
> major. This is shipped as a minor on an explicit project ruling, and is recorded here rather
> than left for you to discover: if you depend on `tokenpak` with `>=1.15,<2.0` or without a pin,
> read the upgrade notes before taking this one.
>
> Measured against 1.15.0 as published, by the project's own snapshot gate
> (`scripts/release_gate/api_snapshot_diff.py`), the removals are:
>
> ```
> tokenpak.licensing.TIER_TEAM
> tokenpak.licensing.TIER_ENTERPRISE
> tokenpak.cli.commands.license_cmd.DEFAULT_UPGRADE_URL
> tokenpak.cli.commands.status.DEFAULT_UPGRADE_URL
> tokenpak.cli.commands.preview.cmd_preview          # module removed
> tokenpak.cli.commands.preview.register_preview     # module removed
> tokenpak.companion.codex.launcher.FallbackDecision
> tokenpak.companion.codex.launcher.PreflightEvaluation
> tokenpak.companion.codex.launcher.PreflightEvidence
> tokenpak.companion.codex.launcher.PreflightStatus
> tokenpak.companion.codex.launcher.TemporarySessionChoice
> ```
>
> Only the first two are discussed below as breaking; the rest are internal
> surfaces the snapshot gate counts as public, and they are listed so the number
> can be checked rather than taken on trust. `tokenpak preview` itself is
> unaffected — the command works and is on the supported surface; what moved is
> the module that registered it.

### Changed

- **BREAKING — the tier ladder is now `free < pro`.** The two tiers above Pro are
  retired and Pro introduces every feature they used to: `tokenpak_server`,
  `seat_management`, `team_analytics`, `audit_log` and `sla` all resolve to
  `pro`. This supersedes the ladder described under 1.15.0 below.

  No license above Pro could be issued, so every feature gated above Pro was
  unreachable by anyone holding a real license — including `tokenpak_server`,
  which gates the daemon that the paid tier is defined by. A valid Pro licensee
  could not run it.

  `required_tier_for` returns `"pro"` for all of the above. The two retired
  members are gone from the `LicenseTier` enum, so code that names one, or that
  assumes four rungs, no longer imports; the ladder is available from
  `LicenseTier.ladder()` and the tier names from
  `tokenpak.licensing.known_tiers()` rather than being hardcoded.

- The per-key rate-limit table in the (unshipped) intelligence server collapses
  to `free: 20/min`, `pro: 100/min`.

- **TokenPak advertises only the surface it has verified.** The parser exposed 90
  commands; the registry documented 56, and nothing reconciled the two — so 34
  verbs were reachable but undocumented, and some documented verbs had never been
  run end to end. An explicit beta allowlist
  (`tokenpak/core/registry/beta_surface.json`) now names the supported set and
  requires a written reason for every exclusion, paired against the live parser by
  a test so the surface cannot grow or shrink silently.

  **Excluded is not removed.** Every excluded verb still parses and still runs.
  What it loses is a place in *default* discovery: `tokenpak help --all` lists it
  under its own heading with the reason attached, so a reader can tell "this is
  ready" from "this is reachable". Two verbs were being advertised while broken —
  `watch`, listed as a "live terminal savings dashboard" while its own help says
  it is not implemented, and `last`, a stub — and both now say so.

  `tokenpak --help` is default discovery too, and it was a separate
  hand-maintained list that no test paired against the allowlist. It went on
  advertising five excluded verbs, `last` among them, described as "Show details
  of last compressed request" while the command printed a first-run welcome
  banner. It now lists only supported commands, and a test holds it there.
  `fingerprint`, `optimize`, `prune` and `template` work and are still reachable;
  they lost the listing, not the implementation.

- **`tokenpak status` leads with runtime, routing, and context operation; savings
  follow.** Before "how much did this save me" comes "is it running, is my client
  actually routed through it, and is it operating on my context". The routing line
  is new: a running proxy with nothing pointed at it produces no savings and no
  data, and that state was previously indistinguishable from "working, but idle".

- **One lifecycle observer across `setup`, `start`, `stop`, `restart`, `logs`,
  `doctor` and the menu.** Each of these previously decided for itself what
  "running" meant, and they did not agree — after a successful `tokenpak setup`,
  `tokenpak stop` answered "No proxy PID file found. Is the proxy running?" about
  a proxy it had just started. `start` now verifies the child before recording
  its PID and returns documented exit codes; `stop` distinguishes no PID on
  record, a stale PID it cleared, and a process it signalled; a proxy started by
  something else is reported as a warning rather than a green "running", because
  `stop` and `restart` will not act on it.

- **`config.yaml` is canonical; `config.json` is read-compatibility only**, and
  `doctor` resolves through the shared resolver — so completing setup no longer
  leaves it reporting "no config" and sending you back to the wizard. An
  unreadable config is now distinguished from an absent one.

- **`setup` is scriptable** (`--profile`, `--port`, `--yes`) and honours
  `TOKENPAK_PORT`. EOF is no longer consent: piping `/dev/null` previously
  selected a profile, wrote config, and spawned a daemon with nothing answered.

- **Public output describes two editions, TokenPak and TokenPak Pro.** The
  internal entitlement taxonomy still decides what a license unlocks, but tiers
  above Pro are no longer presented as plans, `plan` no longer prints a price
  column against rows with no purchase path, and `features` speaks in editions.

- **Package maturity drops from Production/Stable to Alpha.** It claimed
  Production/Stable while `preview` printed simulated numbers and `setup`
  reported success for a proxy that had not started.

- **TIP expands to "TokenPak Integrity Protocol", not "Integration Protocol."**
  The prior expansion invited a transport reading that the specification and the
  product constitution both explicitly deny. The acronym, the wire contract, the
  schemas and the version are unchanged — TIP-1.0 remains TIP-1.0. Prose only.

- **`describe_tier()` returns edition names, not tier names.** It now answers
  "TokenPak" / "TokenPak Pro" where it previously answered "Free" / "Pro",
  because every caller was rendering it to a user. The tier name is still
  available from `internal_tier_label()` for diagnostic surfaces that genuinely
  need it. The signature is unchanged; only the strings differ.

### Added

- `tokenpak.licensing.known_tiers()` — tier names in ascending capability
  order, so callers rendering a tier list do not hardcode one.

### Fixed

- **`preview` reported numbers nothing had measured.** It computed
  `output = len(text.split()) * 0.65` and printed four fixed block names
  unrelated to the input; JSON input with no whitespace reported **-900% savings
  with negative block counts**. It now runs the real compression pipeline and
  enforces a result contract — non-negative counts, `saved == input - output`,
  ratio in [0,1], measured duration, pipeline-assigned block identities, and
  `applied=false` when compression would expand the input. Provenance is
  mandatory: input digest, byte length, source, tokenizer id, stages run,
  version.

  `preview` also accepts conversation input (message array, provider request
  body, or JSONL), because savings come from redundancy across turns. A
  single-turn preview reports 0% and says why, rather than implying the product
  does not work.

- **`stats` displayed a 1% measured saving as "99.4% token reduction."** It
  reported `(1 - ratio) * 100` while the proxy records `ratio` as `saved/input` —
  already a savings fraction. It also reported a hardcoded proxy port rather than
  this install's.

- **Bare `tokenpak` printed a hardcoded "5.6% compression"** regardless of the
  data, and attributed 95% of all savings to whichever model sorted first. Both
  are gone: compression is reported from the measurement or not at all, and the
  model line reports usage, which is what it can observe.

- **A session with zero requests reported "$0.00 saved."** Nothing was measured,
  so there is nothing to report; it now says that. Removed the ~5%/~30%/~40%
  savings claims from the profile menu — savings are reported after measurement,
  not promised at the moment of choice.

- **Auto-start had been inert.** `setup` spawned `runtime/proxy.py`, a four-line
  re-export with no `__main__` block, so the child exited 0 without serving —
  and `setup` printed a checkmark on both branches because it probed the port
  rather than the child, wrote the PID before any check, and never called
  `poll()`. It now requires a live PID plus a healthy endpoint before claiming
  success, and captures startup output for diagnosis. `/health` reports `pid` so
  ownership can be verified rather than inferred from "something answered".

- **No new install used the canonical home.** The first-run marker was bound at
  import time to `~/.tokenpak/.seen_intro`, so any verb — including `version` —
  created the legacy directory before anything else ran, pinning resolution to it
  for the life of the install. Path semantics are now split: reads resolve
  compatibility-first, writes never target the legacy directory, and
  `resolve_existing()` searches both.

- **Importing the proxy config created a directory as a side effect.**
  `proxy/config.MONITOR_DB` resolved at import in write mode, so importing the
  module created `~/.tpk` — which, on a machine configured in the legacy home,
  flipped config resolution to a directory containing no config. Resolution now
  happens on first use, here and in the compression dictionary, instruction
  table, fingerprint cache, event log, debug log, goals, and vault config.

- **The profile you chose was never loaded.** `core/config_loader.CONFIG_PATH`
  was a module-level constant bound to `~/.tokenpak/config.yaml` while `setup`
  writes `~/.tpk/config.yaml`, so the loader read a file that did not exist and
  returned an empty config — and `TOKENPAK_HOME` had no effect on the proxy at
  all.

- **The home directory's 0700 guarantee did not hold.** The first-run marker
  created it at the process umask (0775) and the previous implementation
  deliberately never re-chmoded. `ensure_home()` now targets the write home and
  repairs group/world bits; user config is written 0600 and the license file is
  written owner-only.

- **Reading telemetry no longer creates state or reads another install's.**
  `status` reported companion savings from a hardcoded legacy path, so an install
  using `TOKENPAK_HOME` or the canonical home showed `$0.00` prompt-side no
  matter what the companion had saved. It now resolves across homes and returns
  "unavailable" rather than zero when no journal exists anywhere.

- **`tokenpak upgrade` opened a URL that returns HTTP 404.** It was in top-level
  help, beginner help, the command registry, and a footer printed on *every*
  `tokenpak status` run — so the most-displayed call to action in the product was
  a dead link. The verb is now a hidden compatibility shim that opens nothing and
  reports that public Pro enrollment is unavailable; there is no default URL, and
  `--print-url` exits non-zero rather than printing a fabricated destination.

- **Every verb the parser accepts is now runnable.** `cli/commands/preview.py`
  imported `Compressor` from `compression.core`, which does not exist, so it
  would have raised `ImportError`; it is deleted rather than wired. `packaging`
  was imported unguarded at two undeclared callsites, which crashed `update` with
  a bare traceback and silently degraded `doctor`'s update check.

- **Companion wrappers answered the wrong question.** `tokenpak claude --help`
  printed Claude Code's help, not TokenPak's; `tokenpak codex --help` did the
  same and provisioned 23 files for a client that may not be installed.
  Launching with the client absent provisioned 21 files and failed with exit
  120 — a code TokenPak never chose and documented nowhere. A preflight now
  names the missing prerequisite and exits 4, and `cli/exit_codes.py` defines
  every code TokenPak assigns.

- **`doctor` emitted 27 raw escape sequences into every piped run**, including
  the paste-your-output bug-report flow. Its colour output now routes through the
  shared formatter, which honours `NO_COLOR`, `TOKENPAK_NO_COLOR`, and `isatty`.
  Two remediation hints that could not work are corrected: `doctor` pointed at
  `setup` for routing (setup routes no client) and at a vault subcommand that
  does not exist.

### Removed

- **BREAKING — the `crewai` optional extra.** `pip install tokenpak[crewai]` no longer installs
  crewai.

  It still *succeeds*, which is the part worth knowing. pip treats an extra a package does not
  declare as a warning, not an error: the command exits 0 and installs TokenPak without crewai.
  So the failure does not appear at install time where you would see it — it appears later as an
  `ImportError` from your own code. If you rely on that extra to pull crewai in, install it
  yourself.

  It was the only path by which `chromadb` entered the dependency graph, and every published
  chromadb 1.x is covered by CVE-2026-45829 with no fixed release available — crewai pins
  `chromadb~=1.1.0`, so there was no version to move to and the constraint was not ours to relax.
  Carrying an unfixable critical advisory for an integration that is not a focus was the wrong
  trade, so the extra is gone rather than documented around.

  **No capability is lost.** The CrewAI adapter under `tokenpak/sdk/crewai/` never imported crewai —
  it is a set of context and handoff wrappers — and it continues to work unchanged. If you use it,
  install crewai yourself alongside TokenPak:

  ```
  pip install tokenpak crewai
  ```

  **Read this before following that line.** Installing crewai yourself brings `chromadb~=1.1.0` and
  therefore CVE-2026-45829, exactly as the extra did. The advisory left TokenPak's dependency graph;
  it did not stop existing for anyone who takes this path. See `SECURITY.md` for what that does and
  does not mean. We would rather say this plainly than let the removal read as a fix it is not.

  This also removes the `json-repair` advisory (GHSA-xf7x-x43h-rpqh), which reached the project only
  through the same path, and drops the resolved dependency set from 256 packages to 196 — including
  `uvicorn`'s optional speedups (`httptools`, `uvloop`, `watchfiles`), which chromadb was the sole
  requester of. Nothing imports them and installs from PyPI never had them.

  **On the absence of a deprecation window.** This project normally requires notice before removing a
  published surface. Removal here was directed by the project owner on the record, on the grounds
  that the integration is not a focus and carrying an unfixable critical advisory for it is not a
  trade worth making. That ruling is what authorises the compressed timeline; it is recorded here
  rather than left implicit.

### Upgrade notes

`pip install --upgrade tokenpak`. No data migration is required and no stored
state is rewritten on upgrade.

**Breaking changes.** Two, both listed above:

1. `LicenseTier.TEAM` and `LicenseTier.ENTERPRISE` no longer exist. Code that
   names either, or that assumes the ladder has four rungs, will not import. Read
   the ladder from `LicenseTier.ladder()` and the names from
   `tokenpak.licensing.known_tiers()` instead of hardcoding them. No entitlement
   is lost: every feature those tiers introduced now resolves to `pro`.
2. `pip install tokenpak[crewai]` no longer resolves. The CrewAI adapter under
   `tokenpak/sdk/crewai/` is unaffected and continues to work — it never imported
   crewai. If you use it, install crewai yourself, and read the security note
   above and in `SECURITY.md` before you do.

**Migration — where TokenPak keeps its files.** This release repairs path
resolution that was binding at import time, and the practical effect is that a
*new* install now uses the canonical home (`~/.tpk`) where previously any command
— including `tokenpak version` — created the legacy `~/.tokenpak` first and
pinned resolution to it.

Existing installations are not moved and do not need to act. Reads resolve
compatibility-first and search both locations, so an install that already lives
in `~/.tokenpak` keeps working, keeps its journals readable, and keeps its
configuration. Writes go to the canonical home. If you want a single location,
`tokenpak home migrate` moves it explicitly. Directory permissions are repaired
in place on first use: the home becomes `0700` and configuration `0600`, which
the previous implementation created at the process umask and then deliberately
never corrected.

**Behaviour you may notice, and should.** Several surfaces that printed numbers
now print less. `preview`, `stats`, `status`, `diff` and bare `tokenpak` report a
measurement or state that none was taken; they no longer fall back to a
constructed figure. A session with no requests reports that nothing was measured
rather than `$0.00 saved`. `start`, `stop` and `setup` return documented exit
codes where they previously returned `None` on every path, so a script doing
`tokenpak start && tokenpak status` will now see a failed boot instead of
succeeding through it. If you have automation that greps for the old strings or
relies on exit code 0 from a failed start, it needs a look.

`tokenpak upgrade` no longer opens a URL. Public Pro enrollment is not open, and
the address it used returns HTTP 404; the verb remains as a shim that says so.

**Rollback:** `pip install tokenpak==1.15.0`. Configuration and stored telemetry
written by 1.16.0 remain readable by 1.15.0 — the format is unchanged and only
resolution order and permissions differ. An install that 1.16.0 moved to the
canonical home is still found by 1.15.0's own compatibility search; if you would
rather be explicit, set `TOKENPAK_HOME`.

**Known issues.** Package maturity is declared Alpha in this release, down from
Production/Stable. That is a correction, not a regression: the previous claim was
made while `preview` printed simulated numbers and `setup` reported success for a
proxy that had not started. Both are fixed here; the classifier moves back up when
a candidate passes the beta gates end to end.

**Deprecations.** `config.json` is read-compatibility only — `config.yaml` is
canonical and is what `setup` writes. The legacy home `~/.tokenpak` remains
readable and is not scheduled for removal in this release.

## [1.15.0] — 2026-07-24

> Minor release: a canonical, importable map from paid features to the license
> tier that introduces them, replacing per-consumer assumptions about which tier
> a feature belongs to.

### Added

- **Canonical license tier and feature map** (`tokenpak.agent.license`). Exposes
  `LicenseTier`, `TIER_FEATURES`, and `required_tier_for(feature)`. Tiers form an
  ordered ladder — `free < pro < team < enterprise` — and each feature is
  introduced by exactly one tier, so `required_tier_for` returns the *lowest*
  tier that grants it.

  Before this, a consumer with no way to look a feature up had to assume one.
  The map makes the answer explicit and checkable: `tokenpak_server`,
  `seat_management`, and `team_analytics` resolve to `team`; the nine Pro
  features — including `multipak_capture` — resolve to `pro`; `audit_log` and
  `sla` resolve to `enterprise`.

### Fixed

- **Enterprise tier features are no longer unassigned.** The map initially
  shipped with an empty enterprise list, leaving `audit_log` and `sla` — both in
  active use — with no tier anywhere. That is worse than an absent feature:
  `required_tier_for` returns `None` for both "no such feature" and "feature
  exists but is unassigned", so a caller cannot tell a typo from a gap. Both are
  now assigned, and a regression test rejects an empty feature list on any paid
  tier.

### Upgrade notes

Purely additive; no migration required. Nothing in an existing installation
changes behaviour, and no previously granted entitlement is altered — the map
records tier membership, it does not itself gate anything. Consumers that
hardcoded a tier for these feature names should switch to `required_tier_for`.

**Rollback:** `pip install tokenpak==1.14.0`. The added module is standalone, so
downgrading only removes it.

**Known issues:** none specific to this release.

**Breaking changes / deprecations:** none.

## [1.14.0] — 2026-07-21

> Minor release: verified Codex contention recovery with typed diagnostics,
> fail-closed eligibility, truthful temporary-lineage receipts, and a restored
> health compatibility contract.

### Added

- **Verified-contention recovery for `tokenpak codex`.** When another Codex
  process is confirmed to hold the shared local history, an interactive launch
  can start a temporary session with a new history lineage. The prompt defaults
  to refusal and is never offered to CI, non-interactive launches, incomplete
  diagnostics, permission/storage errors, corruption, or unknown failures.
- **Typed preflight evidence and policy decisions.** Launcher diagnostics now
  distinguish clear, live-holder, stopped-holder, last-verified-live timeout,
  incomplete inspection, permission/storage failure, cancellation, corruption,
  and unknown failure outcomes. The five additive launcher types are retained
  as public beta API symbols and captured by the generated API snapshot.
- **Temporary-lineage accounting evidence.** Codex accounting receipts can
  additively report the original preflight result, fallback eligibility and
  outcome, selected session class, continuity mode, prior-history attachment,
  and the bridge policy version under `tokenpak_setup`. Existing receipt fields
  and meanings are unchanged.
- **Provider-neutral first-receipt path.** `tokenpak codex` can route an
  already-authenticated Codex OAuth session through a healthy local proxy while
  preserving the client's selected/default model. API keys and explicit model
  overrides remain optional client-specific alternatives.

### Fixed

- **Contention no longer exposes a technical recovery command as the ordinary
  UX.** Interactive users receive a consequence-oriented choice after verified
  contention while the original safe refusal remains the default.
- **Fallback failures preserve the original diagnosis.** Selection or
  provisioning failure records both outcomes and exits through the original
  refusal rather than silently choosing another session location.
- **Queued monitor writes honor their target database.** The process-global
  telemetry writer now switches its guarded SQLite connection when queued work
  targets another monitor database, preventing cross-instance, recovery-tool,
  and test traffic from being committed to the previous database.
- **Responses routing distinguishes OAuth from API keys.** OAuth
  `/v1/responses` traffic is rewritten to the ChatGPT Codex backend, while
  `Bearer sk-...` Responses traffic remains on the OpenAI API endpoint. Native
  zstd request entities are decoded before safe processing, and protected
  system/developer policy is never capsulized.
- **`/health` compatibility is explicit.** The canonical endpoint returns one
  documented, uncached basic schema on every request; `?deep=true` adds bounded
  diagnostics. The deprecated route-mixin payload and one-second cache remain
  available for one compatibility window instead of changing silently.
- **Health performance checks distinguish startup from steady state.** Cold
  listener admission and first-health observations are recorded separately
  from the blocking warmed 100-requests-per-second check, preventing startup
  backlog from being mislabeled as sustained endpoint latency. The warmed gate
  retains full latency vectors, host telemetry, and fail-closed provenance.
- **Strict typing and generated-surface provenance are enforced.** Runtime,
  CLI, proxy, telemetry, vault, and SDK boundaries now pass the strict type
  baseline, while public API snapshots are generated from and correlated to
  the declared source checkout.

### Compatibility

- **Breaking changes:** none. Existing launch defaults, environment overrides,
  non-interactive behavior, shared-history refusal semantics, and the supported
  basic `/health` response remain valid.
- **Deprecations:** none.
- **Migration:** none. Existing TokenPak and Codex configuration files require
  no changes.
- **Version note:** v1.13.1 was never released; v1.14.0 is the next published
  candidate after v1.13.0.

### Known limitation

The temporary session does not attach the prior shared Codex history and is not
the future broker-managed parallel-continuity architecture. Normal later
launches return to the governed shared lineage.

### Upgrade

```bash
pip install --upgrade tokenpak==1.14.0
```

### Rollback

```bash
pip install tokenpak==1.13.0
```

No TokenPak-home cleanup is required. A temporary Codex history created by
v1.14.0 remains retention-managed and does not replace the shared lineage.

## [1.13.0] — 2026-07-20

> Minor release: deterministic memory configuration, Receipt v1 inspection,
> bounded managed-request admission, safer launch defaults, and a broad
> proxy/release-tooling reliability pass.

### Added
- **Deterministic memory optimization.** `tokenpak config optimize` can plan,
  apply, inspect, and roll back a process-local MemoryGuard configuration
  derived from physical and cgroup memory limits. Managed state uses canonical
  hashes, atomic writes, drift detection, and an exact preimage receipt;
  runtime environment overrides remain authoritative.
- **Receipt v1 proof objects and debug inspection.** Request accounting can
  emit structured proof receipts, and `tokenpak debug receipt` can inspect a
  recorded request without changing request behavior.
- **Managed-request admission.** Classified background traffic can use a
  bounded concurrency gate with deterministic queue-full and wait-timeout
  responses; unclassified traffic retains its existing pass-through behavior.
- **Per-client launcher permission defaults.** `tokenpak permissions launcher`
  can persist `inherit`, `approval-bypass`, `sandbox-bypass`, or `full-bypass`
  for TokenPak-launched Codex sessions, plus the supported Claude Code subset.
  Bypass modes require explicit confirmation, leave client config files
  untouched, warn on every affected launch, fail closed on invalid state, and
  retain `permissions set fleet` as a full-bypass compatibility alias.
- **Cross-platform process and service helpers.** CLI maintenance and launcher
  paths share a platform abstraction for process discovery, signaling, and
  service lifecycle behavior on Linux, macOS, and Windows.
- **Read-only dashboard layouts.** `tokenpak dashboard --layout ... --json`
  exposes home, dispatch, spend, debug, and multi-instance views without adding a
  mutating dashboard control plane.
- **Typed unsupported-stateful-surface helper.** Callers can build a stable
  `stateful_api_unsupported` remediation payload. The helper is additive and
  dormant: no route handler invokes it, and callers remain responsible for the
  route-appropriate HTTP status.

### Improved
- **Proxy lifecycle and memory return.** Idle client retirement, cleanup
  bounds, listener admission, vault-index memory return, and MemoryGuard
  ownership are coordinated so cleanup remains bounded and in-flight work is
  not retired prematurely.
- **Streaming fidelity for short event streams.** Short server-sent-event
  responses are forwarded incrementally while preserving byte-for-byte
  pass-through behavior.
- **Telemetry database canonicalization.** A dry-run-first migration helper
  identifies legacy database locations, fails closed on incompatible targets,
  and preserves source data rather than overwriting it.
- **Dispatch and licensing diagnostics.** Discovery, dry-run ledger, install
  truth, doctor, and fail-closed licensing paths have expanded regression
  coverage and more consistent CLI behavior.

### Fixed
- **Optimization savings use token units.** Receipt fields and legacy fixtures
  no longer present token counts as currency-shaped values.
- **Optional dependency declarations match imports.** Dashboard-serving extras
  now include Jinja2 and python-multipart, and optional compression/import
  guards report missing capabilities truthfully.
- **SDK dependency floors are coherent.** The TypeScript SDK keeps compatible
  Jest/ts-jest tooling and declares the tested axios runtime floor without an
  unrelated major toolchain upgrade.

### CI
- GitHub Actions use current checkout, setup-python, upload-artifact, and
  cross-workflow artifact-download releases with matching gate-inventory
  hashes.
- The public-API snapshot now captures the service-layer optimization,
  provider-usage, and routing symbols exposed by the regularized
  `tokenpak.services` package. This is additive; no public symbol is removed.
- The development lock now includes the import-contract checker and its graph
  engine, keeping local architecture validation reproducible.
- The release audit now composes the complete test, quick smoke, architecture,
  clean-wheel demo, passthrough-performance, byte-fidelity, documentation, and
  public-safety gates with fail-closed evidence handling.

### Docs
- Add the MemoryGuard optimizer guide and Receipt v1 reference.
- Refresh generated CLI reference and current-release limitation metadata.

### Upgrade

```bash
pip install --upgrade tokenpak==1.13.0
```

No additional steps are required.

### Rollback

If `tokenpak config optimize --apply` was used, restore its recorded preimage
before downgrading:

```bash
tokenpak config optimize --rollback
pip install tokenpak==1.12.0
```

Otherwise, no TokenPak-home cleanup is required; run only the `pip install`
command above.

## [1.12.0] — 2026-07-10

### Added
- **Codex receipt-only launch mode.** `--receipt-only` (requiring
  `--receipt-out` and `--run-id`, mutually exclusive with `--budget` and
  `--install-only`) lets a launch emit accounting receipts without installing
  the TokenPak mechanism, and launches without a request body now produce
  **no-body accounting receipts** so accounting stays truthful instead of
  silently dropping those events.
- **Canonical staging-to-public promotion tooling.**
  `scripts/promote-staging-to-public.sh` and
  `scripts/promotion-drift-report.sh` codify the promotion train between the
  staging and public repositories, with a path hold-list at
  `.github/public-promotion-hold.txt`.

### Fixed
- **The no-picker menu fallback is interactive.** On terminals without the
  arrow-key picker (Windows consoles without a `termios` backend, pipes, dumb
  terminals), `tokenpak` with no arguments now prompts for a numbered
  selection instead of printing an inoperable menu and returning to the shell.
- **Trigger and macro command actions no longer run through the host shell.**
  Config- and user-provided command strings are shlex-parsed into an argv
  vector and executed with `shell=False`; shell metacharacters are passed
  literally rather than interpreted, closing a quoting/injection hazard. An
  explicit `shell:` prefix remains available for trusted commands and emits a
  warning, bare TokenPak subcommands are resolved against the live CLI
  registry, and command failures and timeouts are handled structurally so
  trigger daemons and CLI callers never crash.

### Docs
- Add a value-proof guide for `tokenpak prove` and remove stale lifetime
  savings examples from current docs.

## [1.11.3] — 2026-07-08

> **Release note:** version **1.11.2 was tagged but never published to PyPI.**
> Its release run stopped fail-closed at the test gate on a concurrency safety
> check for the Codex skills installer — no build, GitHub Release, or PyPI
> artifact was produced and the last published release remained 1.11.1. 1.11.3
> carries the intended 1.11.2 changes (below), plus the installer fix.

### Fixed
- **Codex skills installer never exposes an empty skill directory under
  concurrent installs.** A replaced skill is now retired as a timestamped
  generation and reclaimed only once it is old enough that no reader can still
  be enumerating it, instead of being deleted the instant it is superseded.
  Because `os.replace` only rebinds a name, a reader (Codex scanning the skills
  directory) that opened the old directory just before the swap could otherwise
  observe it emptied mid-scan; retaining the prior generation until it ages out
  closes that window.

## [1.11.2] — 2026-07-08

> Patch release: tighten the runnable examples guidance, repair user recipe
> overlay loading, and keep release validation stable for the packaged branch.

### Fixed
- **Runnable examples path clarified.** README and examples documentation now
  explain that top-level example files are delivered through the public source
  tree rather than bundled inside the PyPI wheel, with clone/archive paths and a
  credential-free `examples/basic_compression.py` smoke path.
- **User recipe overlays load from the TokenPak home.** The compression recipe
  engine now loads optional user recipes from the resolved TokenPak home
  `recipes/` directory and allows those recipes to intentionally shadow bundled
  defaults.
- **Companion guide tool reference corrected.** The companion guide now matches
  the shipped MCP tool registry and documents vault search/retrieval as indexed
  vault block lookup rather than structured Pak recall.

### CI
- **Release validation branch checks stabilized.** Release workflow artifact
  labels now tolerate slash-containing branch names, and release rehearsal
  snapshot validation uses the same canonical install shape as the release
  workflow.

## [1.11.1] — 2026-07-06

> Patch release: ship the CLI command registry in built distributions so a clean
> wheel install renders the full command list.

### Fixed
- **Command registry ships in wheels.** `tokenpak/core/registry/commands.json` is now
  declared as package data, so a clean `pip install` renders the full command list
  (`tokenpak help` / `help --all`) instead of reporting `0 commands`. The
  distribution-contents gate now asserts the registry file in every built wheel and
  sdist, and the registry's tier labels and the `start` usage string were corrected to
  match the shipped open-source CLI verbs.

## [1.11.0] — 2026-07-06

> Minor release: the batch reviewed and merged under the 2026-07-06 non-author review gate —
> two new dashboard capabilities plus five correctness and packaging fixes.

### Added
- **Dashboard v2 JSON foundation.** `tokenpak dashboard --json` now emits a versioned
  `dashboard.v2.0` snapshot with measured/not-measured semantics and source labeling, backed by
  the new `tokenpak.platform` capability-detection package. Legacy TUI and fleet-wide views are
  adapted to the new contract.
- **Dashboard SSH tunnel launcher.** New `tokenpak dashboard connect` / `disconnect` subcommands
  establish and tear down an SSH-tunneled dashboard session (ControlMaster-based, with liveness
  and health probing); `--public` is repositioned as the advanced non-tunneled mode.

### Fixed
- **Wheel ships companion runtime files.** Companion shell hooks, codex skills, and the companion
  guide are now included in built distributions; on clean installs the codex hooks no longer fail
  with exit 127. The distribution-contents gate now asserts these files in every built wheel and
  sdist.
- **Context-window table corrected and consolidated.** Stale 200K entries for 1M-window models
  were corrected against provider-published metadata, current models were added, and the table now
  lives in the models registry as the single source (spend-guard thresholds key off real windows;
  unknown-model conservative fallback unchanged).
- **No fabricated model attribution.** Logging and forecast paths no longer default a missing
  model id to a real model name; unknown stays unknown and cost is never attributed to a
  fabricated id.
- **Shutdown telemetry record persisted.** Proxy shutdown now writes its summary record to the
  telemetry events file instead of silently dropping it on a missing method.
- **Response stop reason captured.** The proxy records `stop_reason` for non-streaming and
  streaming responses, so refusals returned as HTTP 200 are distinguishable from successful
  completions in monitoring data. Forwarded bytes remain untouched.

### CI
- `actions/checkout` bumped v4 → v7 across workflows (gate-inventory manifest refreshed).


## [1.10.4] — 2026-07-05

> **Release note:** version **1.10.3 was tagged but never published to PyPI.** Its release run
> stopped fail-closed at the test gate (a release-only `pytest-timeout` enforcement issue in the
> dev/full test shapes — no build, GitHub Release, or PyPI artifact was produced) and PyPI remained
> at 1.10.2. 1.10.4 carries the intended 1.10.3 changes (below), plus the release-test fix.

### Fixed
- **Release test-gate stability.** A health-check integration test that polls `/health` for up to
  ~30 seconds by design is given a per-test timeout above the global 30-second cap, so it is no
  longer killed under the enforced `pytest-timeout` in the dev/full test shapes. Release-mechanics
  only — no runtime behavior change.

## [1.10.3] — 2026-07-05

> Patch release: a curated set of concurrency, durability, and telemetry-truth fixes for the
> SQLite-backed stores and the proxy connection lifecycle. No new capabilities or CLI surface.

### Fixed
- **Concurrency and crash-durability across the SQLite-backed stores.** The companion
  journal/budget store, the dispatch effect ledger, the telemetry store, the spend-guard pending
  store, and the monitor write path now use atomic writes, single-owner database resolvers, and
  uniqueness keys that hold under concurrent access and across crash/restart. Dedupe keys prevent
  double-counting and cost accounting reflects actual spend.
- **Telemetry/monitor windowing and write-truth.** `get_stats` now windows on the parsed
  timestamp, so an N-hour window is a real N-hour window (previously a same-date row could be
  counted regardless of the hour). Monitor write failures surface as dropped-row diagnostics
  instead of being lost silently.
- **Proxy connection lifecycle and breaker accounting.** Session-client leases are released
  exactly once — a streaming-request construction failure no longer double-releases and
  prematurely closes a client another in-flight request still holds — and breaker/concurrency-gate
  accounting races are closed.
- **Queued telemetry flushed on shutdown.** Request rows still queued for the monitor's background
  writer are drained to disk on a clean shutdown instead of being dropped when the writer thread
  exits.
- **Runtime state scoped under `TOKENPAK_HOME`.** Monitor database resolution honors
  `TOKENPAK_HOME` so runtime state stays within the configured home directory.

## [1.10.2] — 2026-07-03

> **Release note:** version **1.10.1 was never released.** Its release pipeline runs stopped
> fail-closed at the public-API snapshot gate (no build, GitHub Release, or PyPI artifact was
> produced) and the `v1.10.1` tag was retired. 1.10.2 carries the intended 1.10.1 changes
> below, plus the corrected public-API snapshot.

### Fixed
- **Proxy upstream transport reliability.** Transient upstream failures (connection resets,
  server disconnects, retryable 5xx honoring `Retry-After`) are now retried with a bounded,
  policy-driven recovery before any bytes reach the client — and never after streaming output
  has started. Failed-request recovery metadata is persisted with credentials redacted.
- **Connection pool no longer hands retries a dead connection.** A pooled client that raises a
  transport error is evicted (identity-checked) so the retry gets a fresh connection; evicted,
  idle-reaped, and LRU-displaced clients are retired and closed after a grace period instead of
  being closed while requests are still in flight on them. New pool metrics `evicted_clients`
  and `retired_pending_close`; pool timeouts are env-tunable via `TOKENPAK_POOL_CONNECT_TIMEOUT`
  and `TOKENPAK_POOL_READ_TIMEOUT`.

### Deprecated
- `tokenpak.proxy.server.MAX_UPSTREAM_RETRIES` is retained as a compatibility alias so existing
  imports continue to work, but it is now non-authoritative and deprecated (planned for removal
  in a future minor release): retry behavior is governed by `UpstreamRetryPolicy`, and
  `TOKENPAK_UPSTREAM_RETRIES` remains the supported operator control. Operator-facing behavior
  is unchanged.

### Release integrity
- **Public-API snapshot regenerated in the canonical release environment.** The previous snapshot
  had been regenerated against a stale installed package instead of the source tree, which is what
  stopped the 1.10.1 release runs at the snapshot gate. The snapshot now records the bounded-retry
  public surface (`tokenpak.proxy.upstream_retry`, re-exported by `tokenpak.proxy.server` and
  `tokenpak.proxy.server_async`) and drops two symbol records that were never part of the released
  package, plus a host-specific import-error record.

## [1.10.0] — 2026-06-28

### Added
- **TokenPak Dispatch graduates from preview to a released feature.** The `tokenpak dispatch`
  command (intake/routing, Decision Inbox, run-ledger lifecycle, observability) now ships in the
  released `pip install tokenpak` package — the Dispatch engine, its registry/schema data, and the
  user guide are included in the wheel. Delivery/receipt remain an explicit post-alpha preview
  (no live station execution wired yet).

### Fixed
- Importing the package no longer requires FastAPI: `tokenpak savings` (and other core value
  commands) work on a base install without the optional serve/dashboard extra.
- The CLI proxy-version probe now derives the expected version from the package version and reads
  `/health`, instead of a hard-coded value.

### Packaging
- Release wheels now include `budget_config.yaml` and `term_cards.json`; a build-time assertion
  verifies the Dispatch registry/schema data ships.


### Added

- **cli:** `tokenpak upgrade` opens the public Pro upgrade page, supports
  `--print-url` for non-interactive use, and honors `TOKENPAK_UPGRADE_URL`.

### Changed

- **license/status:** Free-tier upgrade guidance now points to
  `https://tokenpak.ai/pro`.

## [1.9.3] — 2026-06-22

Security patch: path-safety hardening for `pak` install and a default-deny CORS
policy on the proxy's content routes. Additive; one behavior change noted below.

### Security
- **pak install:** added a path-traversal guard (archive entries are resolved and
  confirmed within the target directory), symlinked entries are skipped during
  extraction, and checksum-verified messaging is now honest about what was checked.
- **proxy CORS:** the `/tpk/v1/*` JSON routes no longer emit
  `Access-Control-Allow-Origin: *`. CORS is now **default-deny** with an
  exact-origin allowlist.

### Changed
- **proxy CORS (behavior change):** a browser app fetching `/tpk/v1/*` from a
  different origin must now set `TOKENPAK_PROXY_CORS_ORIGINS` (comma-separated
  exact origins). A matching request `Origin` is echoed back with `Vary: Origin`,
  never `*`. CLI / SDK / MCP clients are unaffected — CORS applies to browsers only.

## [1.9.1] — 2026-06-16

Patch release: privacy/security hardening, honest telemetry, license alignment,
and a dispatch reliability fix. Additive; no breaking changes.

### Security & privacy
- **spend-guard:** credential headers (authorization, api-key, cookie, …) are
  never persisted to disk in the spend-guard pending/replay path.

### Added — telemetry
- **telemetry:** honest platform-origin attribution + a coverage metric surfaced
  in `tokenpak doctor`; session/agent/cycle ids threaded into the monitor.db
  write path for accurate per-source accounting.

### Fixed — dispatch
- **dispatch:** delivered dispatch runs now persist a receipt (receipt builder
  wired into the fulfillment flow); dispatch registry/schema files ship in the wheel.

### Docs
- **license:** package READMEs + PYPI_READINESS were previously corrected from `MIT` to
  `Apache-2.0` to match the canonical Apache-2.0 LICENSE.

## [1.9.0] — 2026-06-14

Minor release: a guided **onboarding & lifecycle** pass on the CLI. Additive
to existing behavior; no breaking changes to current workflows.

### Added — onboarding & lifecycle

- **cli:** `tokenpak uninstall` with `--soft` (remove TokenPak config/state,
  leave integrations) and `--hard` (full removal of TokenPak-owned state).
- **cli (doctor):** `tokenpak doctor --lifecycle` summary plus a default
  route-state line so the proxy/route status is visible at a glance.
- **cli (permissions):** permission-tier system for `tokenpak integrate`
  (strict / standard / auto, plus a multi-instance fleet tier) with a new
  `tokenpak permissions` verb, doctor rows, and an opt-in multi-instance fleet
  launcher mode — see `tokenpak permissions --help`.
- **config:** read-only `tokenpak config doctor` diagnostics and a
  `tokenpak config env` provenance view (loaded env vars + precedence), with a
  documented config load-order spec.
- **cli (integrate):** guided cross-shell `tokenpak integrate` flow with
  `--apply` / `--revert`, shell detection, and a `--no-tui` escape hatch for
  non-interactive use.
- **cli (menu):** hardened interactive menu renderer (single alternate-screen
  session, per-command lifecycle, cached non-blocking status strip; honest
  unknown metrics render as a dash, never a fabricated `$0.00`).

### Added

- TokenPak Dispatch (v0.1-alpha, **preview**) — scoped, station-based,
  resumable work packages with a Decision Inbox and delivery receipts.
  Available on the main branch only; **not yet included in a released
  `pip install tokenpak` package**. See `tokenpak dispatch --help`.

### Breaking — install footprint: heavy extras are now opt-in

**Background:** `pip install tokenpak` previously pulled ~5 GB of CUDA/ML wheels (torch, nvidia/\*, transformers, sentence-transformers, scipy, tree-sitter-languages, pandas, litellm, llmlingua) as hard runtime dependencies. This made first-run installs impractical on machines without CUDA or a fast connection.

**What changed:** the six heavy packages listed below have been moved from `[project.dependencies]` to named `[project.optional-dependencies]` extras. Slim installs no longer install those extras by default; user-invoked guarded paths now name the exact `pip install tokenpak[<extra>]` recovery command when an extra is missing.

**Migration:** if your code uses any of the features below, add the corresponding extra to your install command:

| Feature | Add to install command |
|---|---|
| Semantic search / vector embeddings (sentence-transformers) | `pip install tokenpak[retrieval]` |
| Tree-sitter code parsing | `pip install tokenpak[code-compression]` |
| A/B testing optimizer (scipy) | `pip install tokenpak[intelligence]` |
| Pandas data utilities | `pip install tokenpak[data]` |
| LLMLingua prompt compression | `pip install tokenpak[compression]` |
| LiteLLM Router integration | `pip install tokenpak[integrations-litellm]` |
| **Everything (previous default)** | `pip install tokenpak[full]` |

If you previously ran `pip install tokenpak` and relied on retrieval / code-compression / intelligence / compression / integrations-litellm features, you must add the extra to your install. Guarded runtime features raise, warn, or return an error with the correct `pip install` command if the extra is absent.

**Slim install target:** `pip install tokenpak` is intended to stay fast and lightweight on a clean reference machine. The current slim-install smoke test verifies that heavy optional packages are absent from core metadata and the installed slim environment; it does not enforce a disk-size ceiling yet. The `[full]` extra restores the previous behaviour for users who want everything.

### Added — install footprint extras split

- Named extras: `tokenpak[retrieval]`, `tokenpak[code-compression]`, `tokenpak[intelligence]`, `tokenpak[data]`, `tokenpak[compression]`, `tokenpak[integrations-litellm]`, `tokenpak[full]`.
- CI: slim-install smoke test — installs tokenpak with no extras, verifies heavy optional packages are absent from core metadata and the installed slim environment, and imports `tokenpak`, `tokenpak.proxy`, and `tokenpak.proxy.server`.
- CI: full-install matrix — `pip install -e .[full,dev]` + full test suite.
- `tests/test_dependencies_extras.py` — slim-core invariant gate.
- `tests/test_extras_import_guard.py` — lightweight post-demotion gate that asserts each heavy package is absent from `[project.dependencies]` and smoke-tests each guarded import path.

### Changed — import error messages

- `tokenpak/sdk/integrations/litellm/proxy.py` — missing-extra error message updated to suggest `pip install tokenpak[integrations-litellm]` instead of bare `pip install litellm`.

---

## [1.8.0] — 2026-06-06

Minor release: companion **memory-source ingestion** — point the companion at
your own Markdown notes ("bring your own knowledge base"). Additive only: no
changes to existing behavior, no breaking changes.

### Added

- **companion:** memory-source ingest library API — `ingest_from_dir()` and
  `ingest_sources()` in `tokenpak.companion.memory.lesson_ingest` ingest lessons
  from arbitrary directories of Markdown notes (scanned recursively),
  independent of the built-in memory schema. `ingest_sources()` returns a
  per-source status report whose `reason` distinguishes *no source configured*
  from *missing* / *not-a-directory* / *present but no matching files* / *ok*.
- **companion:** `TOKENPAK_COMPANION_MEMORY_DIRS` configuration — an
  OS-path-separator- or comma-separated list of extra memory directories,
  parsed into `CompanionConfig.memory_dirs` (`~` expanded; empty entries
  dropped; fail-open, never raises).
- **companion (MCP):** the `session_info` tool now reports the configured
  `memory_dirs` and surfaces a hint when no memory source is set, so an empty
  ingest is self-explaining.

### Tests & docs

- `tests/test_companion_memory_source.py` — coverage for the env-var parser,
  `ingest_from_dir`, and the `ingest_sources` status contract.
- Companion guide: a "Memory sources — bring your own knowledge base" section
  documenting the env var, the library API, and the MCP surface.

### Internal

- Regenerated the public-API snapshot. Beyond the companion additions above,
  this is a ratchet correction that absorbs already-public `tokenpak.proxy`
  symbols the committed snapshot had drifted from — no new public API in this
  release other than the companion additions.

## [1.7.1] — 2026-06-03

Surgical patch release: fixes, hardening, and public-safety/CI hygiene only. No new
features, no default-behavior changes, no breaking changes. (The install-footprint
extras split remains parked for a future minor — see the Unreleased section below.)

### Fixed

- **proxy:** evict upstream inflight keys when the in-flight counter reaches zero,
  preventing an upstream RSS leak under sustained load.
- **proxy:** consolidate `CLAUDE_CODE_HEADER_ALLOWLIST` to a single canonical definition.
- **companion:** `check_budget` no longer presents its result as authoritative total spend.
- **companion:** lazy-load sentence-transformers so the MCP server starts quickly
  (cold-start fix); launch the MCP server with a safe Python path (`-P`).
- **companion:** defensive guard for truncated provider streams.
- **pakplan:** read Pak recall from `recall.db` instead of a stale `journal.db`.
- **spend-guard:** attribution-clear rolling-cap `402` response body.
- **cli:** banner shows the live installed package version instead of a hardcoded string.
- **paths:** fail-loud subdir allowlist in `_paths.under()` (and allow the `dispatch/` subdir).
- **telemetry:** skip the RBAC admin bootstrap during snapshot generation.

### Changed

- **docs:** audit and compliance CLI stubs reworded as Pro-tier features; corrected
  protocol terminology to TIP per the glossary.

### Dependencies

- Bump `websockets` to `>=16.0`; bump CI actions `codecov-action` 4→6,
  `download-artifact` 4→8, `sticky-pull-request-comment` 2→3.

### Internal

- Suppress the ephemeral RBAC admin password from release-gate snapshot-validation CI
  logs; snapshot generation now sets `TOKENPAK_SNAPSHOT_GEN=1` to skip the first-run
  admin bootstrap during introspection.
- CI: quarantine runner-sensitive perf/SLA tests from the blocking matrix; refresh the
  release-gate workflow-steps snapshot; validate the release tag is reachable from the
  release branch before build; mask functional identifiers in the identity check.

### Note — licensing

- The `tokenpak activate` licensing integration that landed on `main` during the v1.7.0
  line ships to PyPI for the first time in **1.7.1**; users on the published 1.7.0 wheel
  do not have it.

## [v1.7.0] — 2026-05-25

> Corrected 2026-05-29: the Beta-1 CLI surface below was originally listed
> under v1.6.0, but it is absent from the v1.6.0/v1.6.1 released artifact and
> first ships in v1.7.0. Every entry here is backed by code on the release
> commit.

### Added

- Beta-1 CLI surface (first shipped in v1.7.0):
  - `tokenpak tip` (validate / inspect / conformance / doctor / scaffold-adapter)
  - `tokenpak features` + `tokenpak features explain <feature>`
  - `tokenpak pakplan preview / explain / report`
  - `tokenpak home` (path / init / validate / explain / migrate)
  - `tokenpak doctor --conformance` (regression recovery from v1.3.7)
  - `BETA_ONBOARDING.md` + `KNOWN_LIMITATIONS.md`
- `tokenpak._paths` canonical home-resolver covering the `~/.tpk/` boundary.
- OSS `tokenpak activate` consults the Pro daemon's `/v1/features` endpoint
  (2-second timeout, five fail-closed states), via `tokenpak/licensing`.
- Rolling cumulative spend caps for the spend-guard
  (`tokenpak/proxy/spend_guard/rolling_caps.py`) with session/rolling-window
  enforcement and regression coverage.
- Dynamic reasoning-usage parser registry (`tokenpak/services/providers/`)
  with additive monitor.db reasoning-token columns.
- `tokenpak status --fleet` with a `rollup_daily` aggregation table.
- Vault claude-transcript source adapter for the BM25 index
  (`tokenpak/vault/sources/claude_transcript.py`).
- Anthropic prompt-cache TTL attribution telemetry.

### Changed

- Codex companion lifecycle hooks and vault atomic-write hardening
  (`tokenpak/vault/_atomic.py`) ship together; release-gate trust-contract
  follow-ups land on top of the v1.6.0 phases.
- Copy/terminology adopt "Pak / Prompt Packing / Savings Ledger" across the
  dashboard and CLI help.
- Wheel now ships `tokenpak/tip/schemas/*.json`.
- LICENSE display corrected to Apache-2.0 across README / CONTRIBUTING /
  LICENSE_COMMERCIAL (was a stale MIT label).

### Fixed

- Dashboard settings now forbid webhook-URL writes (with regression test).
- Tightened cache-miss UUID attribution for byte-preserved traffic.
- `doctor` surfaces the home-boundary advisory before other checks.
- CI stabilization: workflow concurrency cancellation, scoped release/
  benchmark triggers, fastapi test-collection import guards, regenerated CLI
  reference, and de-flaked time-relative test fixtures.

### Security

- Corrected the SECURITY.md and Code-of-Conduct contact addresses to
  `hello@tokenpak.ai`, and restored three Claude Code plugin hook scripts that
  a module consolidation had dropped while their hook declarations remained.

## [v1.6.1] — 2026-05-17

### Fixed

- `tests/benchmarks/test_load_100rps.py::TestHealthEndpointLoad::test_health_100rps_p99_under_20ms`
  now widens its p99 ceiling from 500ms (strict, local) to 2000ms when
  `CI=true` or `GITHUB_ACTIONS=true`. GitHub Actions shared 4-core runners
  show scheduler-jitter-driven p99 variance an order of magnitude higher
  than dedicated hosts (the v1.6.0 release run measured
  p50=0.7ms / p95=3.8ms / p99=1025.1ms — a tail-only spike, not a
  health-endpoint defect). Local benchmark signal is preserved at 500ms.

### Re-released

- Re-issues the v1.6.0 release surface. The v1.6.0 tag run did not reach
  the build/publish stages, so PyPI and the GitHub Release for v1.6.0
  were never produced. v1.6.1 ships the identical v1.6.0 functional
  surface (see entries below) plus the benchmark-tolerance fix above.

## [v1.6.0] — 2026-05-16

> Corrected 2026-05-29: the Beta-1 CLI surface (`tip` / `features` / `pakplan`
> / `home` / `doctor --conformance`, `tokenpak._paths`, OSS `activate` →
> `/v1/features`, BETA_ONBOARDING / KNOWN_LIMITATIONS) was originally listed
> here but is absent from the v1.6.0/v1.6.1 released artifact; it is now
> attributed to v1.7.0, where it first ships. The entries below are what
> v1.6.0 actually contained.

### Added

- `tokenpak pak create` and `tokenpak pak import` (OSS Beta 1)
- Release-gate trust contract Phases 4r/5/6 — public-API, telemetry-schema,
  and workflow-steps snapshot ratchets enforced by CI on every PR

### Changed

- `tokenpak plan` output is dynamically derived from the live pricing
  index; the previous "TBD" placeholder is gone
- `tokenpak activate` rejects empty / too-short / non-printable /
  placeholder keys before any daemon round-trip
- `tokenpak pak status` no longer triggers a heavy vault index load; the
  status path returns in under 2 seconds on populated vaults
- Wheel ships `tokenpak/_snapshots/*.json` (release-gate snapshot ratchets)

### Fixed

- `pak status` no longer hangs on hosts with populated vault directories

## [v1.5.6] — 2026-05-11

### Repository

- **Release-workflow hardening.** Three workflow-level guards added to `release.yml`:
  - **Tag-source validation.** A public release tag must point to a commit reachable from `origin/main`. Tags pushed against a feature branch (never merged) are rejected before any build or publish. Skipped on `workflow_dispatch` preflight runs and on non-`v*` tags (rehearsal tags).
  - **Action-pin enforcement.** A new `scripts/check-action-pins.sh` scans every `uses: <owner>/<repo>@<ref>` reference in `.github/workflows/` and rejects abbreviated SHA pins (7–39 hex chars). Full 40-character commit SHAs and floating version-tag refs (e.g. `@v4`) remain allowed.
  - **Checksum dist-purity guard.** A named step asserts that `SHA256SUMS` lives at the repository root, never inside `dist/`. The general dist-purity guard already covers this, but the named step produces a diagnostic that points at the v1.5.3 failure mode if it ever recurs.
- **Workflow-step ratchet.** `tokenpak/_snapshots/workflow-steps.json` records the canonical step set for the release workflow; CI fails if a step is added or removed without an accompanying snapshot update.

### Acceptance

- `pytest tests/ -q --tb=short` is green on Python 3.10 / 3.11 / 3.12 / 3.13.
- `pip install tokenpak==1.5.6` from a fresh virtualenv succeeds.
- All public-surface guardrails (`Public layout check`, `Repo Hygiene Check`, `Identity & language check`, `CLI Docs Up-to-date`) pass on `main`.

### No behavior change

This release ships no runtime, CLI, or public-API behavior change. It is a release-workflow hardening patch and the first release cut on the hardened path.

---

## [v1.5.5] — 2026-05-09

### Repository

- **Public-surface cleanup.** Removes workbench artifacts, internal docs, runtime state, and unrelated subdirectories from the public tree; sanitizes user-facing documentation; adds CI guardrails (public-layout-check, identity-language-check) that prevent regression; retires test code whose dependencies are no longer present in the package. The public root layout dropped from 69 entries to 37; the canonical target is 24 and the remaining gap is tracked under follow-up housekeeping.
- **Doc-generator boundary defense.** `scripts/generate-cli-docs.py` now post-processes the rendered CLI reference to strip parenthetical task-ID seeds, deferred-subcommand sections, and integration-example fragments — driven by `scripts/internal-cli-cleanup.txt`. This keeps the public CLI docs clean without requiring source-side argparse edits.
- **Coverage Gate.** The ≥80% threshold step in `.github/workflows/integration.yml` is marked step-level `continue-on-error: true` pending a realistic threshold reset. The gate still runs and the threshold breach is visible in the workflow log; only the merge-blocking effect is suspended.

### Acceptance

- `pytest tests/ -q --tb=short` is green on Python 3.10 / 3.11 / 3.12 / 3.13.
- `pip install tokenpak==1.5.5` from a fresh virtualenv succeeds.
- All public-surface guardrails (`Public layout check`, `Repo Hygiene Check`, `Identity & language check`, `CLI Docs Up-to-date`) pass on `main`.

### No behavior change

This release ships no runtime, CLI, or public-API behavior change. It is a packaging/hygiene patch.

---

## [v1.5.4] — 2026-05-09

### Fixed

- **Release workflow auto-publish.** The publish step in `.github/workflows/release.yml` was failing because `SHA256SUMS` was generated inside `dist/` and the upstream PyPI publish action rejects every file in `dist/` that isn't `*.whl` or `*.tar.gz`. v1.5.4 supersedes v1.5.3 on PyPI and ships the fix:
  - `SHA256SUMS` is now generated at the repository root, never inside `dist/`.
  - The build job uploads two separate artifacts: `dist` (wheels + sdist only) and `checksums` (SHA256SUMS only).
  - A new pre-upload guard fails the build if `dist/` ever contains anything other than `*.whl` / `*.tar.gz`.
  - The publish step runs a second pre-publish guard immediately before invoking the publish action, as defense-in-depth.

### Acceptance

- `pytest tests/ -q --tb=short` is green on Python 3.10 / 3.11 / 3.12 / 3.13.
- `pip install tokenpak==1.5.4` from a fresh virtualenv succeeds.
- The GitHub Release attaches the wheel, the sdist, and `SHA256SUMS`; PyPI receives only the wheel and sdist.

> **Note**: v1.5.3 was tagged on the same day but its publish step failed with `InvalidDistribution: Unknown distribution format: 'SHA256SUMS'`. v1.5.3 is retained as a historical GitHub-tag-only release. Install with `pip install tokenpak==1.5.4`.

---

## [v1.5.3] — 2026-05-09

### Fixed

- **Release-workflow test gate hardened.** The `Run Tests` step in `release.yml` runs against a `[dev]`-only install. Test files that imported optional/external/internal modules unconditionally caused collection errors on the slim install. Each affected test now guards its imports at module load with either `pytest.importorskip(…)` for optional deps installable via extras or a `try/except ImportError → pytest.skip(allow_module_level=True)` for namespace packages where the directory exists in slim OSS but the required submodule isn't bundled.
- **Release workflow contract documented.** The `release.yml` `test` job carries a top-of-job comment block describing what the gate does and doesn't cover, the import-guard contract for optional deps, and the rule "do not bypass via `--ignore`; either fix the test's guard or add the missing extra to this step."
- **Telemetry exports restored** after a partial refactor that left the field shape drifting from the contract.
- **Test-suite stability**: resolved 23 collection errors across two distinct buckets (ghost-path imports + speculative module surfaces); guarded `jsonschema` / `yaml` in config-validator tests so slim install skips cleanly; restored `tests/test_errors.py` against the canonical error-handling path; relocated tests with internal-only dependencies under `tests/_internal/` so OSS-slim collection no longer drags closed-tree fixtures.
- **Python 3.10 collection** guarded `tomllib` (stdlib only on 3.11+); residual import guards across 24 files.
- **Test alignment**: `test_install_claude_code` and `test_setup_wizard` aligned with v1.5.2 production signatures after API drift.
- **Performance/benchmark hermeticity**: hermetic compression benchmark and re-baseline; non-flaky throughput-ratio test (pre-encoded bytes, deterministic timing); load-test timeouts adjusted for CI runner stability.

> v1.5.3 itself is not on PyPI; see v1.5.4 above.

---

## [v1.5.2] — 2026-05-08

### Added — Pak data contracts (TIP capability surface)

- 10 new TIP capability constants under `tokenpak.tip.capabilities` (`tip.pak.{capture,index,recall,hydrate,promote}`, `tip.context.{package,handoff,resume,coverage,policy}`).
- `Pak` and `ContextPackage` frozen dataclasses with full JSON round-trip in `tokenpak.tip.pak` and `tokenpak.tip.context_package`.
- 54 contract tests in `tests/tip/test_multipak_contracts.py`.

### Added — OSS surface for Pak inspection

- Read-only Pak inspection through the Vault adapter (`tokenpak/vault/pak_adapter.py`).
- `/pak/v1/status` and `/pak/v1/inspect/<id>` endpoints in the proxy. Other `/pak/v1/*` endpoints return a structured `not_implemented` response when the optional Pro daemon is absent.
- Standardized `not_implemented` error shape: `{ "error": "not_implemented", "reason": "pro_daemon_required", "detail": "…", "suggested_action": "…", "daemon_state": "…" }`.
- `tokenpak pak status` and `tokenpak pak inspect <id>` CLI commands.
- 100 surface tests in `tests/proxy/test_pak_endpoints.py` and `tests/cli/test_pak_command.py`.

---

## [v1.5.1] — 2026-05-07

### Added — Spend Guard (proxy-side circuit breaker)

- Pre-send circuit breaker that blocks risky requests before they reach the upstream provider. New package `tokenpak/proxy/spend_guard/` (estimator, policy, pending store, intent parser, replay engine, header parser, audit log, orchestrator, session-state). Hooked into `proxy/server.py` immediately after body read, before DLP. Returns HTTP 402 Payment Required with `error.type=tokenpak_spend_guard_blocked` JSON; user releases via Yes/No reply or a `[TIP: allow=once max=$X]` directive; the hard-block ceiling cannot be bypassed. Default `enabled: true` with thresholds `warn=100K/$2`, `block=500K/$10`, `hard_block=1M/$50`, `session_block_cost_usd=$10`. Pricing pulled from `tokenpak.models.get_rates`. Audit log at `~/.tokenpak/spend_guard.db`. New errors `SpendGuardBlocked (TP-ESG01)` and `SpendGuardHardBlocked (TP-ESG02)` in `core/error_handling.py`. User-facing docs at `docs/spend-guard.md`. 149 tests in `tokenpak/tests/test_spend_guard_*.py`.

### Fixed — `tokenpak start` config validator env-var bypass

- `tokenpak/core/config_validator.py` — wired the `ANTHROPIC_API_KEY` (and three other provider env-var) bypass that the missing-`api_keys` suggestion text has always advertised. `_has_env_api_key()` was defined but never called by `_validate_required_fields`, so users following documented setup hit `Required field 'api_keys' is missing` and `tokenpak start` refused to launch. The suggestion text now mentions all three accepted bypass paths (in-config dict / env var / byte-passthrough placeholder). 2 regression tests added.

### Added — proxy auth gate

- `TOKENPAK_PROXY_AUTH_TOKEN` opt-in middleware in `tokenpak/proxy/proxy_auth.py`. Localhost stays trusted; non-localhost requests now require `Authorization: Bearer <token>` whenever the env var is set, else 403. `hmac.compare_digest` for timing-safe comparison; SHA-256 hex of the token populates the new `user_id` column on the SQLite `requests` table and `extra.user_id` on the structured JSON request log — the raw token is never logged. Schema migration is additive (`ALTER TABLE requests ADD COLUMN user_id TEXT DEFAULT ''`), back-compatible with pre-auth-gate rows. The proxy-auth Bearer is stripped before forwarding upstream so the upstream provider only ever sees its own `x-api-key`. Tests in `tests/proxy/test_proxy_auth.py`. Docs at `docs/configuration/proxy-auth.md`.

### Added — headline benchmark CI gate

- `tests/benchmarks/test_headline_claim.py`, `tests/fixtures/headline_corpus.txt` — deterministic 9-message DevOps agent corpus (~8 kB) and a blocking pytest assertion that compression stays in [30, 50]%. Run locally: `make benchmark-headline`.

### Added — proxy-owned REST API

- Proxy `/tpk/v1/*` REST endpoints in `tokenpak/proxy/app_endpoints.py`:
  - `GET /tpk/v1/health` — version, uptime, vault status
  - `GET /tpk/v1/vault/search?q=&limit=` — BM25 search over the vault
  - `GET /tpk/v1/vault/block/{block_id}` — full block content
  - `GET /tpk/v1/budget` — session + daily cost snapshot
  - `GET /tpk/v1/journal/sessions?limit=` — recent journal sessions
  - `GET /tpk/v1/journal/{session_id}?entry_type=&limit=` — journal entries
  - `POST /tpk/v1/journal/{session_id}/entry` — add journal entry
  - `POST /tpk/v1/compress` — head/tail truncate to max_tokens
  - `POST /tpk/v1/optimize` — offline prompt linter report
  - `POST /tpk/v1/tokens/estimate` — token count for text/file
  - `GET /tpk/v1/capsules`, `GET /tpk/v1/capsules/{id}` — memory capsules
  - `GET /tpk/v1/session/info` — proxy environment snapshot
- Localhost-only auth by default; optional `X-TPK-Key` header if `TOKENPAK_PROXY_KEY` is set.

### Added — `tokenpak integrate`

- One-command client setup. Print-mode for 9 supported clients (Claude Code, Cursor, Cline, Continue.dev, Aider, Codex CLI, OpenAI SDK, Anthropic SDK, LiteLLM). `--apply` mode writes configs for clients with stable config formats: Claude Code (`~/.claude/settings.json`), Cursor (platform-specific `settings.json`), Continue.dev (`~/.continue/config.json`), Aider (`~/.aider.conf.yml`). Always backs up before writing and prints a rollback command.

### Added — license / plan / activate / deactivate

- Free-tier defaults today; Pro / Team / Enterprise surface ready. Gated features cataloged in `tokenpak/licensing/_GATES` (single `is_feature_enabled(name)` choke point). License stored at `~/.tokenpak/license.json`.

### Added — `tokenpak compress` and `tokenpak optimize`

- Real implementations replace earlier paywall stubs. Both run offline; `tokenpak compress` detects JSON messages for dedup and supports `--file` / stdin / `--json` / `--verbose`. `tokenpak optimize` reports whitespace bloat, repeated phrases, verbose phrasings.

### Added — 1h cache_control TTL

- `tokenpak/proxy/prompt_builder.py:_cache_control_dict()` reads `TOKENPAK_CACHE_TTL`. Set `1h` to emit `{"type":"ephemeral","ttl":"1h"}` on all cache_control markers, extending the upstream 5-minute default to 1h. Worth the 2x write cost for traffic that fires at >5-minute intervals.

### Added — telemetry SQLite writer

- The `Monitor` class in `tokenpak/proxy/monitor.py` now persists request rows to `~/.tokenpak/monitor.db`. Previously requests were written to JSONL logs only.

### Changed — auto-discover models by default

- `tokenpak/models/_discovery.py` — `auto_start_if_enabled()` now opts IN when an API key is present (was opt-in via `TOKENPAK_MODEL_DISCOVERY=1`). Family-rule inference handles unseen models with no seed edit required.

### Changed — compression on by default

- `ENABLE_COMPACTION` and `BUDGET_CONTROLLER_ENABLED` default to `True`; `COMPACT_THRESHOLD_TOKENS` defaults to `1500` (was `4500`). To restore the legacy passthrough behavior, use `tokenpak serve --safe`.

### Added — Claude Code client-auth pass-through

- When Claude Code sends its own OAuth credentials (`Authorization: Bearer` + `anthropic-beta: oauth-2025-04-20`), the proxy preserves the original request bytes while applying response-side features (cost tracking, logging, budget enforcement). Byte preservation is required because JSON re-serialization changes the request signature, causing upstream billing to route to the wrong quota pool.

### Added — byte-level vault injection

- `proxy.py:_find_system_array_close()`, `_byte_inject_system_block()` — splice vault context directly into the JSON system array at a byte offset without `json.loads` / `json.dumps` round-trip. Preserves all original bytes except the insertion point. Configurable via `TOKENPAK_CC_INJECT_MAX_CHARS` (default 2000) and relevance-gated via `TOKENPAK_CC_INJECT_MIN_QUERY` (default 50 chars).

### Added — full header forwarding for Claude Code

- Client-auth requests forward all headers verbatim (no allowlist filtering) to preserve the request identity used for OAuth quota routing. Includes `x-app`, `X-Stainless-*`, `Content-Type`, and all upstream beta flags.

### Added — TTL-aware cache_control in Anthropic adapter

- `tokenpak/proxy/adapters/anthropic_adapter.py:_body_has_explicit_ttl()` — `inject_system_context()` now detects requests with explicit `ttl` values in cache_control blocks and skips adding default ephemeral markers that would violate the upstream TTL ordering rule.

### Fixed — race conditions and validation

- **Failover iterator thread safety** — `FailoverManager.iter_providers()` now snapshots the provider chain under a lock before iterating, preventing `RuntimeError: dictionary changed size during iteration` when `reload_config()` races with an in-flight iteration.
- **Circuit breaker config reload synchronization** — added `CircuitBreakerRegistry.reload_config()` which re-reads env vars and propagates the new config to all existing breakers under the registry lock.
- **Streaming handler cross-chunk SSE buffering** — `StreamHandler.process_chunk()` now accumulates text in a line buffer and flushes only complete lines into the byte buffer, preventing parse failures when a `data: {…}` SSE event spans two `recv()` calls.
- **Cost tracking failure audit trail** — when `cost_tracker.record_request()` raises, the failure is now logged at `ERROR` level with a structured `COST_TRACKING_FAILURE model=… tokens=…` message instead of a bare `WARNING`.
- **Router Content-Length validation** — `ProviderRouter.route()` now raises `ValueError` when the `Content-Length` header doesn't match the actual body size or is non-numeric, preventing truncated bodies from being silently forwarded upstream.
- **Passthrough config validation** — `PassthroughConfig.__post_init__()` now raises `ValueError` if any header name appears in both `strip_headers` and `safe_to_log`.

### Added — `tokenpak prune` command

- Top-level alias for `tokenpak audit prune`; accepts `--days` (retention window) and `--db` (audit DB path) flags.

### Added — CLI surface consistency test

- `tests/cli/test_help_surface_consistency.py` asserts every command in `tokenpak --help` exits 0 on `<cmd> --help`.

### Added — framework adapters

- **CrewAI adapter** (`tokenpak/adapters/crewai/`) — `TokenPakContext`, `TokenPakCrewAIHook`, `TokenPakCrew`, `TokenPakHandoff`; install with `pip install tokenpak[crewai]`.
- **AutoGen adapter** (`tokenpak/adapters/autogen/`) — `TokenPakConversationHook`, `TokenPakAssistant`, `TokenPakGroupChat`, `compress_messages`; install with `pip install tokenpak[autogen]`.
- **LlamaIndex adapter** (`tokenpak/adapters/llamaindex/`) — `TokenPakSynthesizer`, `TokenPakQueryEngine`, `TokenPakIndex`, `MultiIndexFusion`; install with `pip install tokenpak[llamaindex]`.
- `pyproject.toml` extras: `[crewai]`, `[autogen]`, `[llamaindex]`.

### Removed (with replacement)

| Removed | Resolution | Canonical replacement |
|---|---|---|
| `tokenpak prune` | Implemented as top-level alias | `tokenpak audit prune` (same `--days`, `--db` flags) |
| `tokenpak list-models` | Removed from docs | `tokenpak models` |
| `tokenpak provider-status` | Removed from docs | `tokenpak status` or `tokenpak doctor` |
| `tokenpak provider-force-health` | Removed from docs | `tokenpak doctor --fix` |
| `tokenpak rebuild-vault-index` | Removed from docs | `tokenpak vault repair` |
| `tokenpak cache-stats` | Removed from docs | `tokenpak stats` |
| `tokenpak list-keys` | Removed from docs | No direct replacement — use provider dashboard |
| `tokenpak proxy --config` | Removed from docs | `tokenpak start` with config at `~/.tokenpak/config.yaml` |

---

## [1.5.0] — 2026-05-03

### Added

- Provider failover and circuit-breaker improvements consolidated into stable surfaces.
- Streaming and cache-control work consolidated for production deployment.

---

## [1.0.2] — 2026-03-25

### Fixed

- Improved error handling for malformed YAML configs.
- Hardened streaming chunk parser against partial SSE events.

---

## [1.0.1] — 2026-03-18

### Fixed

- Configuration validation regressions reported after the 1.0.0 release.

---

## [1.0.0] — 2026-03-10

First stable production release.

- Provider-agnostic routing stabilized across Anthropic and OpenAI-compatible paths.
- Stronger fallback orchestration and circuit-breaker behavior under upstream pressure.
- Hardened startup and runtime checks; better diagnostics on failover activation.

---

## [0.9.0] — 2026-02-20

### Added

- Provider-agnostic routing foundation with Anthropic and OpenAI adapter support.
- Vault index: semantic retrieval of compressed context blocks from local markdown vaults.
- Compression pipeline: salience-based extraction, dedup, and token budgeting.
- Telemetry server with SQLite-backed usage tracking.
- Docker image with multi-stage build and non-root runtime.

### Changed

- Migrated from single-file proxy to modular `tokenpak/` package structure.

### Fixed

- Streaming SSE passthrough race condition under concurrent requests.

---

## [0.5.0] — 2026-01-28

### Added

- Initial compression pipeline: document and code salience extractors.
- Vault block indexing with FAISS-backed retrieval (replaced with SQLite in v0.9).
- Basic CLI: `tokenpak serve`, `tokenpak status`, `tokenpak doctor`.
- WebSocket proxy endpoint (`/ws`) for real-time streaming clients.
- Benchmark suite for proxy passthrough, vault lookup, and routing decisions.

### Changed

- Moved from monolithic `proxy.py` to layered architecture (router → adapter → backend).

---

## [0.3.0] — 2026-01-10

### Added

- Core HTTPS proxy with pass-through to Anthropic Messages API.
- Token counting and budget enforcement hooks.
- Request/response logging with configurable verbosity.
- Initial recipe system for reusable compression configurations.

---

## [0.1.0] — 2025-12-20

### Added

- Initial prototype: HTTPS proxy rewriting requests to the upstream Messages API.
- Proof-of-concept context compression reducing prompt tokens by ~30%.
- Basic configuration via YAML file.
- Single-file `proxy.py` implementation.
