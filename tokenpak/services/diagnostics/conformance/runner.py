"""Conformance-check runner consumed by ``tokenpak doctor --conformance``.

Thin wrapper over the same primitives the SC-06 pytest suite uses:

- ``tokenpak_tip_validator.validate_capability_set``
- ``tokenpak_tip_validator.validate_profile``
- ``tokenpak_tip_validator.validate_against`` (schema-level)
- ``tokenpak.services.diagnostics.conformance.install`` (observer)
- The canonical ``SELF_CAPABILITIES_*`` sets
- The static JSON manifests at ``tokenpak/manifests/``

Never writes to stdout/stderr. Returns ``list[CheckResult]`` so the CLI
layer decides the presentation. Absorbs the paper-spec slice of
``scripts/tip_conformance_check.py`` + adds the live-emission checks
the pytest suite proved in SC-06.

Intentionally narrow: no HTTP boot, no Layer-C scenario replay — those
are pytest-only paths. ``tokenpak doctor --conformance`` is the
operator-readable "is my install conformant?" self-check.
"""
from __future__ import annotations

import json
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from tokenpak.services.diagnostics.checks import (
    CheckResult,
    CheckStatus,
)


def _ok(name: str, summary: str, *details: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.OK, summary=summary, details=list(details))


def _fail(name: str, summary: str, *details: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.FAIL, summary=summary, details=list(details))


def _warn(name: str, summary: str, *details: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.WARN, summary=summary, details=list(details))


# ── tooling preflight ────────────────────────────────────────────────────


def _ensure_schemas_reachable() -> None:
    """Point the validator at our vendored schemas if no root is set.

    The ``tokenpak-tip-validator`` PyPI package (0.1.0) does not bundle
    the TIP schemas in its wheel — it resolves them from
    ``$TOKENPAK_REGISTRY_ROOT`` or the parent directory of its own
    installed location, neither of which works in a plain
    ``pip install tokenpak`` environment.

    SC-07 vendors the schemas inside the tokenpak package at
    ``tokenpak/_tip_schemas/{tip,manifests}/``. This helper points
    ``TOKENPAK_REGISTRY_ROOT`` at that vendored tree when the env var
    is unset so the runner works from an installed wheel without a
    separate registry checkout. User overrides win — if the operator
    has set ``TOKENPAK_REGISTRY_ROOT`` intentionally, we don't clobber
    it.
    """
    import os

    if os.environ.get("TOKENPAK_REGISTRY_ROOT"):
        return
    try:
        # Validator expects ``<root>/schemas/tip/capabilities.schema.json``,
        # so the vendored tree mirrors that layout:
        #   tokenpak/_tip_schemas/schemas/tip/*.json
        #   tokenpak/_tip_schemas/schemas/manifests/*.json
        vendor_root = files("tokenpak").joinpath("_tip_schemas")
    except Exception:  # noqa: BLE001
        return
    try:
        cap = vendor_root.joinpath("schemas/tip/capabilities.schema.json")
        if cap.is_file():
            # Resolve to a plain string path so the validator's
            # Path(env)/schemas/tip/... composition works.
            # MultiplexedPath (namespace packages) can't round-trip
            # to a string directly; fall back to the underlying path.
            try:
                vendor_path = str(vendor_root)
            except Exception:  # noqa: BLE001
                return
            os.environ["TOKENPAK_REGISTRY_ROOT"] = vendor_path
    except Exception:  # noqa: BLE001
        return


def _tooling_preflight() -> CheckResult | None:
    """Return a FAIL CheckResult if the validator is unavailable; else None.

    The runner returns exit code 2 (tooling error) up the stack when this
    signals. Every downstream check depends on the validator.
    """
    try:
        import tokenpak_tip_validator  # noqa: F401
    except ImportError as exc:
        return _fail(
            "validator",
            "tokenpak-tip-validator not importable",
            f"ImportError: {exc}",
            "Install via: pip install 'tokenpak-tip-validator>=0.1.0'",
        )
    _ensure_schemas_reachable()
    # Now confirm the schemas are actually resolvable — some installs
    # have the validator package but no schemas on disk.
    try:
        from tokenpak_tip_validator.schema import load_schema

        load_schema("capabilities")
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "validator",
            "TIP schemas not reachable by validator",
            f"{type(exc).__name__}: {exc}",
            "Set TOKENPAK_REGISTRY_ROOT to a tokenpak/registry checkout,",
            "or reinstall tokenpak (SC-07 ships schemas under tokenpak/_tip_schemas/).",
        )
    return None


# ── checks ───────────────────────────────────────────────────────────────


def _check_capability_set() -> CheckResult:
    from tokenpak_tip_validator import validate_capability_set

    from tokenpak.core.contracts.capabilities import SELF_CAPABILITIES

    result = validate_capability_set(list(SELF_CAPABILITIES))
    if result.ok:
        return _ok(
            "capability-set",
            f"{len(SELF_CAPABILITIES)} labels well-formed",
        )
    msgs = [f.message for f in result.errors()[:3]]
    return _fail(
        "capability-set",
        f"{len(result.errors())} finding(s)",
        *msgs,
    )


def _check_profile(profile: str, caps: frozenset[str]) -> CheckResult:
    from tokenpak_tip_validator import validate_profile

    result = validate_profile(profile, capabilities=list(caps))
    errors = [f for f in result.errors()]
    if not errors:
        return _ok(
            f"profile:{profile}",
            f"{len(caps)} capabilities satisfy profile",
        )
    msgs = [f"{f.code}: {f.message}" for f in errors[:3]]
    return _fail(
        f"profile:{profile}",
        f"{len(errors)} finding(s)",
        *msgs,
    )


def _load_shipped_manifest(filename: str) -> dict[str, Any]:
    """Load a manifest via importlib.resources — works from wheels."""
    path = files("tokenpak").joinpath(f"manifests/{filename}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check_manifest_schema(filename: str, schema: str) -> CheckResult:
    from tokenpak_tip_validator import validate_against

    try:
        manifest = _load_shipped_manifest(filename)
    except FileNotFoundError:
        return _fail(
            f"manifest:{filename}",
            "file not shipped in package",
            f"Expected at tokenpak/manifests/{filename} (check package-data wiring).",
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            f"manifest:{filename}",
            f"unreadable: {exc}",
        )
    result = validate_against(schema, manifest)
    if result.ok:
        return _ok(
            f"manifest:{filename}",
            f"validates against {schema} schema",
        )
    msgs = [f"{f.code}: {f.message}" for f in result.errors()[:3]]
    return _fail(
        f"manifest:{filename}",
        f"{len(result.errors())} schema finding(s)",
        *msgs,
    )


def _check_manifest_sync(
    filename: str, canonical: frozenset[str], canonical_name: str
) -> CheckResult:
    """Manifest capabilities array must equal the canonical frozenset."""
    try:
        manifest = _load_shipped_manifest(filename)
    except Exception as exc:  # noqa: BLE001
        return _fail(f"sync:{filename}", f"manifest unreadable: {exc}")
    manifest_caps = frozenset(manifest.get("capabilities") or [])
    extra = sorted(manifest_caps - canonical)
    missing = sorted(canonical - manifest_caps)
    if not extra and not missing:
        return _ok(
            f"sync:{filename}",
            f"{len(manifest_caps)}/{len(canonical)} capabilities match {canonical_name}",
        )
    details = []
    if missing:
        details.append(f"canonical only: {missing}")
    if extra:
        details.append(f"manifest only: {extra}")
    return _fail(
        f"sync:{filename}",
        f"capabilities drift from {canonical_name}",
        *details,
    )


def _check_telemetry_emission() -> CheckResult:
    """Drive Monitor.log under an observer and schema-validate the TIP row.

    Mirrors Layer-A assertion `test_scenario_telemetry_row_validates`
    with a synthetic row. Never touches monitor.db beyond a temp dir.
    """
    from tokenpak_tip_validator import validate_against

    from tokenpak.proxy.monitor import Monitor
    from tokenpak.services.diagnostics import conformance as _conformance

    captured: list[dict] = []

    class _Obs:
        def on_telemetry_row(self, row):
            captured.append(dict(row))

        def on_response_headers(self, *_):
            pass

        def on_companion_journal_row(self, *_):
            pass

        def on_capability_published(self, *_):
            pass

    uninstall = _conformance.install(_Obs())
    try:
        with tempfile.TemporaryDirectory() as td:
            m = Monitor(db_path=f"{td}/monitor.db")
            m.log(
                model="claude-opus-4-7",
                input_tokens=100,
                output_tokens=25,
                cost=0.0,
                latency_ms=42,
                status_code=200,
                endpoint="https://api.anthropic.com/v1/messages",
                cache_read_tokens=0,
                cache_creation_tokens=0,
                cache_origin="unknown",
                request_id="doctor-conformance",
            )
    finally:
        uninstall()

    if not captured:
        return _fail(
            "emission:telemetry",
            "Monitor.log produced no observer row",
            "SC-02 chokepoint wiring may be missing.",
        )
    row = captured[-1]
    result = validate_against("telemetry-event", row)
    if not result.ok:
        msgs = [f"{f.code}: {f.message}" for f in result.errors()[:3]]
        return _fail(
            "emission:telemetry",
            f"telemetry row failed schema ({len(result.errors())} finding(s))",
            *msgs,
        )
    return _ok(
        "emission:telemetry",
        "Monitor.log row validates against telemetry-event schema",
    )


def _check_journal_emission() -> CheckResult:
    """Drive JournalStore.write_entry under an observer and validate."""
    from tokenpak_tip_validator import validate_against

    from tokenpak.companion.journal.store import JournalStore
    from tokenpak.services.diagnostics import conformance as _conformance

    captured: list[dict] = []

    class _Obs:
        def on_telemetry_row(self, *_):
            pass

        def on_response_headers(self, *_):
            pass

        def on_companion_journal_row(self, row):
            captured.append(dict(row))

        def on_capability_published(self, *_):
            pass

    uninstall = _conformance.install(_Obs())
    try:
        with tempfile.TemporaryDirectory() as td:
            js = JournalStore(db_path=Path(td) / "journal.db")
            js.write_entry("doctor-conformance", "preflight", entry_type="auto")
    finally:
        uninstall()

    if not captured:
        return _fail(
            "emission:journal",
            "JournalStore.write_entry produced no observer row",
            "SC-02 chokepoint wiring may be missing.",
        )
    row = captured[-1]
    result = validate_against("companion-journal-row", row)
    if not result.ok:
        errs = result.errors()
        # Installed tokenpak-tip-validator 0.1.0 (PyPI) predates the
        # companion-journal-row schema added in SC-01. Treat
        # ``schema.unavailable`` as a WARN (validator version gap),
        # not a FAIL (conformance failure). Operator action: upgrade
        # the validator when a PyPI release ships the new schema.
        if any(f.code == "schema.unavailable" for f in errs):
            return _warn(
                "emission:journal",
                "validator predates companion-journal-row schema",
                "Upgrade: pip install -U tokenpak-tip-validator (≥ version shipping companion-journal-row).",
                f"Observer still captured row shape: {sorted(row.keys())}",
            )
        msgs = [f"{f.code}: {f.message}" for f in errs[:3]]
        return _fail(
            "emission:journal",
            f"journal row failed schema ({len(errs)} finding(s))",
            *msgs,
        )
    return _ok(
        "emission:journal",
        "JournalStore.write_entry row validates against companion-journal-row schema",
    )


# ── public entry point ───────────────────────────────────────────────────


def run_conformance_checks() -> list[CheckResult]:
    """Run every conformance check; never raises. Ordered stable.

    Order is deterministic so CI logs diff cleanly across runs:
    tooling preflight -> capability-set -> profile x2 -> manifest
    schema x2 -> manifest sync x2 -> emission x2.
    """
    from tokenpak.core.contracts.capabilities import (
        SELF_CAPABILITIES_COMPANION,
        SELF_CAPABILITIES_PROXY,
    )

    preflight = _tooling_preflight()
    if preflight is not None:
        return [preflight]

    return [
        _check_capability_set(),
        _check_profile("tip-proxy", SELF_CAPABILITIES_PROXY),
        _check_profile("tip-companion", SELF_CAPABILITIES_COMPANION),
        _check_manifest_schema("tokenpak-proxy.json", "provider-profile"),
        _check_manifest_schema("tokenpak-companion.json", "client-profile"),
        _check_manifest_sync(
            "tokenpak-proxy.json",
            SELF_CAPABILITIES_PROXY,
            "SELF_CAPABILITIES_PROXY",
        ),
        _check_manifest_sync(
            "tokenpak-companion.json",
            SELF_CAPABILITIES_COMPANION,
            "SELF_CAPABILITIES_COMPANION",
        ),
        _check_telemetry_emission(),
        _check_journal_emission(),
    ]


def summarize(results: list[CheckResult]) -> dict[str, int]:
    """Roll up a ``{ok, warn, fail}`` count dict for the runner's exit logic."""
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for r in results:
        counts[r.status.value] += 1
    return counts


def exit_code_for(results: list[CheckResult]) -> int:
    """Operator-readable exit contract:

    - 0: every check OK (WARN is still 0 for strict operator use).
    - 1: ≥1 check FAIL and tooling is OK (conformance failure).
    - 2: tooling error — validator unimportable, manifests unreadable.
    """
    # Special-case the single-result tooling preflight case. Its name
    # is "validator"; anything else that fails is a conformance
    # failure.
    if (
        len(results) == 1
        and results[0].status is CheckStatus.FAIL
        and results[0].name == "validator"
    ):
        return 2
    counts = summarize(results)
    return 1 if counts["fail"] > 0 else 0


__all__ = [
    "run_conformance_checks",
    "summarize",
    "exit_code_for",
]
