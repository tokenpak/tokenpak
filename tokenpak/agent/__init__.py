"""tokenpak.agent — mixed-status compatibility subtree. Do not assume dead.

Two different statuses coexist here; do not generalize from one to the
other:

- ``agent.cli.commands.serve`` / ``agent.cli.commands.metrics`` are
  confirmed dead in-repo AND externally (verified against every current
  ``tokenpak-paid`` call site) — pure re-export shims left only for
  backward compatibility, each carrying its own module-level
  ``DeprecationWarning``. Safe to remove in a future release.
- ``agent.license`` (``validator.py``) is the OPPOSITE of dead: it is a
  live, actively-imported external contract read directly by the private
  `tokenpak-paid` package (`tokenpak_paid.entitlements`) to gate real paid
  CLI commands. See ``agent.license``'s own module docstring before
  touching it — it must NOT be deprecated, renamed, or removed without a
  coordinated change on that side first.

This `__init__.py` (and the ones in agent/cli and agent/cli/commands)
exists so this package is a regular (non-namespace) package: that makes it
visible to the import-linter architecture-layers contract, which governs
it as the outermost layer so it may depend on tokenpak.cli and below but
nothing may depend on it. Do not add new code here — use the canonical
locations referenced above.
"""
