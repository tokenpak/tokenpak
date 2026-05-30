# SPDX-License-Identifier: Apache-2.0
"""``tokenpak tip`` CLI subcommand — TokenPak Integration Protocol surface.

Beta 1 surface (regression recovery from v1.3.7's ``doctor --conformance``;
the v1.3.7 ``services/diagnostics/conformance/`` runner was removed in
the modular refactor without a CLI replacement). This command file is
the new canonical home — TIP contracts live in ``tokenpak.tip.*`` and
the schemas live in ``tokenpak.tip.schemas/``; this CLI is the thin
operator surface over them.

Subcommands:
    inspect          List known TIP capability labels grouped by family.
    validate <ref>   Validate a capability label or a JSON file against
                     a TIP schema.
    conformance      Run TIP self-conformance checks (importability of
                     contracts, schema integrity, capability-set sanity).
                     Exit codes follow the doctor convention:
                       0  all checks pass (or only WARN)
                       1  ≥1 FAIL
                       2  tooling error (contracts unimportable)
    doctor           Conformance + environment summary (alias for
                     ``tokenpak doctor --conformance`` from the dedicated
                     verb-side). Same exit-code contract.
    scaffold-adapter <name>
                     Emit a starter capability-declaring adapter file.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Public CLI entry: build the parser
# ---------------------------------------------------------------------------


def build_tip_parser(sub: Any) -> None:
    """Register the ``tokenpak tip`` subcommand on ``sub``."""
    p_tip = sub.add_parser(
        "tip",
        help="TokenPak Integration Protocol — validate, inspect, conformance",
        description=(
            "TIP is the protocol layer that adapter providers and "
            "platform integrations declare against. This verb family "
            "exposes the OSS-side validation, inspection, and "
            "self-conformance surface."
        ),
    )
    tipsub = p_tip.add_subparsers(dest="tip_action", required=False)

    p_inspect = tipsub.add_parser(
        "inspect", help="List known TIP capability labels"
    )
    p_inspect.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text"
    )
    p_inspect.set_defaults(func=cmd_tip_inspect)

    p_validate = tipsub.add_parser(
        "validate", help="Validate a capability label or JSON file"
    )
    p_validate.add_argument(
        "ref",
        help=(
            "Either a capability label (e.g. 'tip.compression.v1') or a "
            "filesystem path to a JSON document to check"
        ),
    )
    p_validate.add_argument(
        "--schema", default=None,
        help=(
            "Schema name (e.g. 'tip-capabilities.v1') when validating a "
            "JSON file. Required for file mode."
        ),
    )
    p_validate.add_argument(
        "--json", action="store_true", help="Emit JSON result"
    )
    p_validate.set_defaults(func=cmd_tip_validate)

    p_conform = tipsub.add_parser(
        "conformance", help="Run TIP self-conformance checks"
    )
    p_conform.add_argument(
        "--json", action="store_true", help="Emit JSON result envelope"
    )
    p_conform.set_defaults(func=cmd_tip_conformance)

    p_doctor = tipsub.add_parser(
        "doctor", help="TIP doctor — conformance + environment summary"
    )
    p_doctor.add_argument(
        "--json", action="store_true", help="Emit JSON result envelope"
    )
    p_doctor.set_defaults(func=cmd_tip_doctor)

    p_scaffold = tipsub.add_parser(
        "scaffold-adapter", help="Emit a starter TIP adapter file"
    )
    p_scaffold.add_argument("name", help="Adapter name (e.g. 'my-platform')")
    p_scaffold.add_argument(
        "--output", "-o", default=None,
        help="Output file path (default: ./<name>_adapter.py)",
    )
    p_scaffold.set_defaults(func=cmd_tip_scaffold)

    p_tip.set_defaults(func=lambda a: p_tip.print_help())


# ---------------------------------------------------------------------------
# Conformance runner — used by `tip conformance`, `tip doctor`, and the
# `tokenpak doctor --conformance` flag (regression-recovery from v1.3.7).
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    status: str  # "PASS" | "WARN" | "FAIL"
    summary: str
    details: str = ""


def run_conformance_checks() -> list[CheckResult]:
    """Run the Beta 1 TIP conformance check set.

    Order is deterministic so CI logs diff cleanly.
    """
    results: list[CheckResult] = []

    # 1. Contracts importable
    try:
        from tokenpak import tip as _tip  # noqa: F401
        results.append(CheckResult(
            name="contracts.importable",
            status="PASS",
            summary="tokenpak.tip package importable",
        ))
    except Exception as exc:
        results.append(CheckResult(
            name="contracts.importable",
            status="FAIL",
            summary="tokenpak.tip not importable",
            details=str(exc),
        ))
        return results  # tooling error — no further checks meaningful

    # 2. Capability set non-empty + label-pattern conformant
    import re

    label_re = re.compile(r"^(tip|ext)\.[a-z0-9._-]+$")
    try:
        from tokenpak.tip.capabilities import ALL_OPTIMIZATION_CAPABILITIES
        bad = [c for c in ALL_OPTIMIZATION_CAPABILITIES if not label_re.match(c)]
        if not ALL_OPTIMIZATION_CAPABILITIES:
            results.append(CheckResult(
                name="capabilities.set_nonempty",
                status="FAIL",
                summary="ALL_OPTIMIZATION_CAPABILITIES is empty",
            ))
        elif bad:
            results.append(CheckResult(
                name="capabilities.label_pattern",
                status="FAIL",
                summary=f"{len(bad)} capability label(s) violate the regex",
                details=", ".join(sorted(bad)[:10]),
            ))
        else:
            results.append(CheckResult(
                name="capabilities.label_pattern",
                status="PASS",
                summary=f"{len(ALL_OPTIMIZATION_CAPABILITIES)} capability labels valid",
            ))
    except Exception as exc:
        results.append(CheckResult(
            name="capabilities.label_pattern",
            status="FAIL",
            summary="cannot evaluate ALL_OPTIMIZATION_CAPABILITIES",
            details=str(exc),
        ))

    # 3. Multipak capability set is a subset of the global set
    try:
        from tokenpak.tip.capabilities import (
            ALL_OPTIMIZATION_CAPABILITIES,
            MULTIPAK_CAPABILITIES,
        )
        leaked = MULTIPAK_CAPABILITIES - ALL_OPTIMIZATION_CAPABILITIES
        if leaked:
            results.append(CheckResult(
                name="capabilities.multipak_subset",
                status="FAIL",
                summary=f"{len(leaked)} multipak labels missing from ALL set",
                details=", ".join(sorted(leaked)[:10]),
            ))
        else:
            results.append(CheckResult(
                name="capabilities.multipak_subset",
                status="PASS",
                summary="multipak capabilities ⊆ ALL_OPTIMIZATION_CAPABILITIES",
            ))
    except Exception as exc:
        results.append(CheckResult(
            name="capabilities.multipak_subset",
            status="WARN",
            summary="cannot check multipak subset",
            details=str(exc),
        ))

    # 4. Schemas readable + parseable
    schema_dir = _schema_dir()
    if not schema_dir.is_dir():
        results.append(CheckResult(
            name="schemas.directory",
            status="FAIL",
            summary=f"schema directory not found at {schema_dir}",
        ))
    else:
        files = sorted(schema_dir.glob("*.json"))
        if not files:
            results.append(CheckResult(
                name="schemas.directory",
                status="WARN",
                summary=f"no .json schemas found in {schema_dir}",
            ))
        else:
            bad: list[str] = []
            for f in files:
                try:
                    json.loads(f.read_text(encoding="utf-8"))
                except Exception as exc:
                    bad.append(f"{f.name}: {exc}")
            if bad:
                results.append(CheckResult(
                    name="schemas.parseable",
                    status="FAIL",
                    summary=f"{len(bad)} schema file(s) failed to parse",
                    details="\n".join(bad[:5]),
                ))
            else:
                results.append(CheckResult(
                    name="schemas.parseable",
                    status="PASS",
                    summary=f"{len(files)} TIP schema(s) parsed cleanly",
                ))

    # 5. Pak contract importable (prerequisite)
    try:
        from tokenpak.tip import pak as _pak  # noqa: F401
        results.append(CheckResult(
            name="contracts.pak_schema",
            status="PASS",
            summary="tokenpak.tip.pak importable",
        ))
    except Exception as exc:
        results.append(CheckResult(
            name="contracts.pak_schema",
            status="WARN",
            summary="tokenpak.tip.pak unavailable",
            details=str(exc),
        ))

    # 6. Each per-contract module importable
    for mod in (
        "cache_contract",
        "compression_contract",
        "fidelity_contract",
        "optimization_contract",
        "route_contract",
        "telemetry_contract",
        "trace_contract",
        "context_package",
    ):
        try:
            __import__(f"tokenpak.tip.{mod}")
            results.append(CheckResult(
                name=f"contracts.{mod}",
                status="PASS",
                summary=f"tokenpak.tip.{mod} importable",
            ))
        except Exception as exc:
            results.append(CheckResult(
                name=f"contracts.{mod}",
                status="FAIL",
                summary=f"tokenpak.tip.{mod} unimportable",
                details=str(exc),
            ))

    return results


def summarize(results: Iterable[CheckResult]) -> dict:
    """Roll up to PASS/WARN/FAIL counts + overall verdict."""
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    if counts["FAIL"] > 0:
        verdict = "fail"
    elif counts["WARN"] > 0:
        verdict = "pass_with_warnings"
    else:
        verdict = "pass"
    return {"counts": counts, "verdict": verdict}


def exit_code_for(summary: dict) -> int:
    """Map a summary verdict to the doctor exit-code convention."""
    if summary["verdict"] == "fail":
        return 1
    return 0


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def cmd_tip_inspect(args: Any) -> int:
    """Print TIP capability labels grouped by family prefix."""
    try:
        from tokenpak.tip.capabilities import ALL_OPTIMIZATION_CAPABILITIES
    except Exception as exc:
        print(f"✗ tokenpak tip inspect — cannot import capabilities: {exc}",
              file=sys.stderr)
        return 2

    grouped: dict[str, list[str]] = {}
    for cap in sorted(ALL_OPTIMIZATION_CAPABILITIES):
        family = cap.split(".", 2)
        key = ".".join(family[:2]) if len(family) > 1 else cap
        grouped.setdefault(key, []).append(cap)

    if getattr(args, "json", False):
        print(json.dumps({
            "total": len(ALL_OPTIMIZATION_CAPABILITIES),
            "groups": grouped,
        }, indent=2, sort_keys=True))
        return 0

    print(f"TIP capability labels ({len(ALL_OPTIMIZATION_CAPABILITIES)} total)")
    print("─" * 50)
    for family, caps in sorted(grouped.items()):
        print(f"\n  {family}.*")
        for c in caps:
            print(f"    {c}")
    return 0


def cmd_tip_validate(args: Any) -> int:
    """Validate a capability label (string form) or a JSON file (with --schema)."""
    import re

    ref = args.ref
    schema = getattr(args, "schema", None)
    as_json = bool(getattr(args, "json", False))

    label_re = re.compile(r"^(tip|ext)\.[a-z0-9._-]+$")

    # File mode (path-shaped or --schema given)
    is_filey = ("/" in ref or ref.endswith(".json"))
    if is_filey or schema:
        if not schema:
            print(
                "✗ tokenpak tip validate — --schema required when validating a file",
                file=sys.stderr,
            )
            return 1
        path = Path(ref).expanduser()
        if not path.exists():
            print(f"✗ tokenpak tip validate — file not found: {ref}",
                  file=sys.stderr)
            return 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"✗ tokenpak tip validate — invalid JSON: {exc}",
                  file=sys.stderr)
            return 1
        schema_path = _schema_dir() / (
            schema if schema.endswith(".json") else schema + ".json"
        )
        if not schema_path.exists():
            print(f"✗ tokenpak tip validate — schema not found: {schema_path}",
                  file=sys.stderr)
            return 1
        try:
            schema_obj = json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"✗ tokenpak tip validate — schema unparseable: {exc}",
                  file=sys.stderr)
            return 2
        ok, errors = _validate_against_schema(data, schema_obj)
        result = {
            "ref": str(path),
            "schema": schema_path.name,
            "ok": ok,
            "errors": errors,
        }
        if as_json:
            print(json.dumps(result, indent=2))
        elif ok:
            print(f"✅ {path.name} conforms to {schema_path.name}")
        else:
            print(f"✗ {path.name} does not conform to {schema_path.name}",
                  file=sys.stderr)
            for e in errors:
                print(f"   - {e}", file=sys.stderr)
        return 0 if ok else 1

    # Capability label mode
    ok = bool(label_re.match(ref))
    result = {
        "ref": ref,
        "kind": "capability_label",
        "ok": ok,
        "pattern": label_re.pattern,
    }
    if as_json:
        print(json.dumps(result, indent=2))
    elif ok:
        print(f"✅ {ref} — valid TIP capability label")
    else:
        print(f"✗ {ref} — does not match TIP label pattern {label_re.pattern}",
              file=sys.stderr)
    return 0 if ok else 1


def cmd_tip_conformance(args: Any) -> int:
    """Run TIP self-conformance and emit human or JSON output."""
    results = run_conformance_checks()
    summary = summarize(results)

    if getattr(args, "json", False):
        envelope = {
            "tokenpak_version": _tokenpak_version(),
            "checks": [
                {"name": r.name, "status": r.status,
                 "summary": r.summary, "details": r.details}
                for r in results
            ],
            "summary": summary,
            "exit_code": exit_code_for(summary),
        }
        print(json.dumps(envelope, indent=2, sort_keys=True))
        return exit_code_for(summary)

    _render_check_results(results, summary)
    return exit_code_for(summary)


def cmd_tip_doctor(args: Any) -> int:
    """Conformance + environment summary."""
    rc = cmd_tip_conformance(args)
    if not getattr(args, "json", False):
        print()
        print("Environment")
        print("───────────")
        print(f"  tokenpak_version : {_tokenpak_version()}")
        print(f"  python           : {sys.version.split()[0]}")
        print(f"  schema_dir       : {_schema_dir()}")
    return rc


def cmd_tip_scaffold(args: Any) -> int:
    """Emit a starter TIP adapter file under the requested name."""
    name = args.name.strip()
    if not name:
        print("✗ tokenpak tip scaffold-adapter — empty name", file=sys.stderr)
        return 1
    out = Path(args.output) if args.output else Path(f"./{name}_adapter.py")
    if out.exists():
        print(f"✗ tokenpak tip scaffold-adapter — refusing to overwrite {out}",
              file=sys.stderr)
        return 1
    out.write_text(_adapter_template(name), encoding="utf-8")
    print(f"✅ Scaffolded TIP adapter → {out}")
    print("   Next step: declare your capability set + register the adapter.")
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _schema_dir() -> Path:
    """Return the directory holding TIP JSON schemas."""
    return Path(__file__).resolve().parent.parent.parent / "tip" / "schemas"


def _tokenpak_version() -> str:
    try:
        from tokenpak import __version__ as v
        return str(v)
    except Exception:
        return "unknown"


def _render_check_results(results: list[CheckResult], summary: dict) -> None:
    icons = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
    print("TIP self-conformance")
    print("────────────────────")
    for r in results:
        print(f"{icons.get(r.status, '?')} {r.name:38s} {r.status:5s} {r.summary}")
        if r.details:
            for line in r.details.splitlines():
                print(f"     {line}")
    counts = summary["counts"]
    print()
    print(f"Summary: {counts['PASS']} pass / {counts['WARN']} warn / "
          f"{counts['FAIL']} fail   →  verdict: {summary['verdict']}")


def _validate_against_schema(data: Any, schema: dict) -> tuple[bool, list[str]]:
    """Best-effort schema validation.

    Uses ``jsonschema`` when available; otherwise applies a minimal
    fallback validator that checks ``type`` + ``items.pattern`` for the
    array-of-strings schemas that the TIP set ships today. Beta 1 ships
    the fallback on purpose so a fresh install with no extras still
    passes; ``pip install tokenpak[tip]`` would pull the full
    ``jsonschema`` dependency in a future iteration.
    """
    try:
        import jsonschema  # type: ignore
    except ImportError:
        return _fallback_validate(data, schema)
    try:
        jsonschema.validate(instance=data, schema=schema)  # type: ignore[arg-type]
        return True, []
    except jsonschema.ValidationError as exc:  # type: ignore[attr-defined]
        return False, [str(exc.message)]
    except Exception as exc:
        return False, [f"validator error: {exc}"]


def _fallback_validate(data: Any, schema: dict) -> tuple[bool, list[str]]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected == "array":
        if not isinstance(data, list):
            return False, [f"expected array, got {type(data).__name__}"]
        items = schema.get("items", {}) or {}
        item_type = items.get("type")
        item_pat = items.get("pattern")
        if item_pat:
            import re

            pat = re.compile(item_pat)
            for i, v in enumerate(data):
                if item_type == "string" and not isinstance(v, str):
                    errors.append(f"[{i}] not a string")
                elif isinstance(v, str) and not pat.match(v):
                    errors.append(f"[{i}] {v!r} does not match {item_pat}")
        if schema.get("uniqueItems") and len(set(map(str, data))) != len(data):
            errors.append("uniqueItems violation")
    elif expected == "object":
        if not isinstance(data, dict):
            return False, [f"expected object, got {type(data).__name__}"]
        required = schema.get("required", []) or []
        for k in required:
            if k not in data:
                errors.append(f"missing required field: {k}")
    return (len(errors) == 0), errors


def _adapter_template(name: str) -> str:
    return f'''# SPDX-License-Identifier: Apache-2.0
"""TIP adapter scaffold for ``{name}``.

This file was generated by ``tokenpak tip scaffold-adapter {name}``.
Replace the placeholder capability set + register the adapter through
your platform's adapter-registration entrypoint. See the provider
adapter integration guide for the additive-only contract.
"""

from __future__ import annotations

# Declare the TIP capability labels this adapter implements.
# Use only labels from tokenpak.tip.capabilities (or ext.<vendor>.* for
# vendor extensions). Empty set means "no TIP optimizations supported".
CAPABILITIES: frozenset[str] = frozenset({{
    # "tip.compression.v1",
    # "tip.cache.proxy-managed",
}})


def adapter_info() -> dict:
    return {{
        "name": "{name}",
        "capabilities": sorted(CAPABILITIES),
    }}
'''


__all__ = [
    "build_tip_parser",
    "run_conformance_checks",
    "summarize",
    "exit_code_for",
    "CheckResult",
]
