---
---

Release-gate: ratchet the public-API snapshot for the read/write path split.

**Added.** `tokenpak._paths.write_under` — the write-side counterpart to `under()`, for
every callsite whose next statement creates something. The two resolvers existed
already; what was missing was a path builder rooted at the write home, so callers
reached for the read one and created state in whichever directory reads happened to
name.

`tokenpak.proxy.proxy_watchdog` gains `proxy_pid_file`, `watchdog_log`,
`cooldowns_file_path`, `auth_profiles_file` and `configure_logging`.
`tokenpak.core.cooldown` gains `cooldowns_file_path` and `auth_profiles_file_path`.
`tokenpak.core.auth.oauth_manager` gains `auth_profiles_file`.

**Removed from the module surface, not from the snapshot.** Those additions replace
module-level `Path` constants (`PROXY_PID_FILE`, `WATCHDOG_LOG`, `COOLDOWNS_FILE`,
`AUTH_PROFILES_FILE`) that were bound at import. The snapshot never captured them —
it does not record `Path`-valued module attributes — so this is not a tracked
public-symbol removal and needs no declaration. It is recorded here anyway, because
anyone who imported one by name will notice, and because the reason is worth stating:
a constant built at import time is a resolution decision frozen before the process
knows anything, and in the watchdog's case the module then created a directory next
to it. Importing it was enough to decide where the installation lived.

`CooldownManager` takes `Optional[Path]` arguments defaulting to `None` rather than
resolved paths. A default argument is evaluated once at import — the same freezing in
a second form.
