#!/usr/bin/env python3
"""gen_api_snapshot.py — generate tokenpak/_snapshots/public-api.json.

Per Std 30 §7 / R7 (public-API snapshot). Walks the `tokenpak` package and
emits a sorted JSON array of importable public names — every attribute that
does not begin with an underscore on every importable submodule.

Output schema:
    {
      "version": "1.0",
      "generated_at": "<ISO8601>",
      "package_version": "<__version__>",
      "symbols": [
        {"module": "tokenpak", "name": "TelemetryCollector"},
        ...
      ]
    }

Usage:
    python3 scripts/release_gate/gen_api_snapshot.py [--check] [--out <path>]

Flags:
    --check      Exit 1 if generated snapshot differs from on-disk snapshot.
    --out PATH   Override default output path.

Authority: Std 30 §7 (R7), ratified 2026-05-09.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import sys
import time
from pathlib import Path

# Mark this process as a snapshot-generation run before any tokenpak module
# is imported, so library first-run side effects (e.g. RBAC admin bootstrap)
# are skipped and cannot pollute deterministic snapshot output.
os.environ.setdefault("TOKENPAK_SNAPSHOT_GEN", "1")

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "tokenpak" / "_snapshots" / "public-api.json"

# Third-party library re-exports that are NOT TokenPak public API. Their capture
# by the package walk is environment-dependent (present only when an optional
# extra is installed, e.g. faiss via [retrieval]), which makes the snapshot
# non-deterministic ("phantom" add/remove noise). Exclude them explicitly so the
# snapshot deterministically reflects the TokenPak-owned released surface.
_THIRD_PARTY_REEXPORTS: set[tuple[str, str]] = {
    ("tokenpak.vault.retrieval.vector_local", "faiss"),
    ("tokenpak.proxy.server_extra.websocket_proxy", "WebSocketServerProtocol"),
}


_ABS_PATH_PAREN_RE = None  # lazily compiled
_SIDECAR_RE = None  # lazily compiled


def _format_import_error(e: BaseException, module_name: str = "") -> str:
    """Format an import-time exception as a host-independent IMPORT_ERROR string.

    Output shape: ``<IMPORT_ERROR: <ExceptionType>: <message>>``

    Three normalizations make this host-independent so the snapshot does
    not drift between developer hosts (where sidecar integration packages
    like ``autogen_tokenpak`` / ``crewai_tokenpak`` are editable-installed)
    and CI (where they are not):

      1. Parenthesized absolute filesystem paths are stripped from the
         message — for example a developer-host build-path or a CI
         runner-path embedded in an import error message.

      2. When the message indicates "cannot import name 'X' from '<sidecar>'"
         (the developer-host shape, raised because the sidecar is
         editable-installed but has an unresolved internal import), both
         the exception type AND message are rewritten to the canonical
         ``ModuleNotFoundError: No module named '<sidecar>'`` that the
         gateless CI environment would emit.

      3. When the failing import is under ``tokenpak.sdk.<sidecar>.*`` (the
         walk site of a sidecar integration), the error is normalized to
         ``ModuleNotFoundError: No module named '<sidecar>_tokenpak'``
         regardless of the actual exception. This covers the case where
         the sidecar itself loads on the developer host but its transitive
         imports fail (e.g. ``tokenpak.sdk.crewai.examples.basic_usage``
         raises ``No module named 'tokenpak.agent.agentic'`` on the
         developer host but ``No module named 'crewai_tokenpak'`` on CI —
         both indicate the same effective state: the sidecar integration
         is not usable in this environment).

    Without this normalization the snapshot captures host-specific noise
    and the release-gate snapshot check drifts on every developer-host
    regeneration.
    """
    import re

    global _ABS_PATH_PAREN_RE, _SIDECAR_RE
    if _ABS_PATH_PAREN_RE is None:
        _ABS_PATH_PAREN_RE = re.compile(r"\s*\(/[^)]*\)")
    if _SIDECAR_RE is None:
        _SIDECAR_RE = re.compile(
            r"cannot import name '[^']+' from '([a-z_]+_tokenpak)'"
        )

    # Walk-site sidecar normalization (transform 3 above)
    m_walk = re.match(r"^tokenpak\.sdk\.([a-z_]+)\.", module_name)
    if m_walk:
        sidecar = m_walk.group(1)
        return f"<IMPORT_ERROR: ModuleNotFoundError: No module named '{sidecar}_tokenpak'>"

    msg = _ABS_PATH_PAREN_RE.sub("", str(e))
    m = _SIDECAR_RE.search(msg)
    if m:
        return f"<IMPORT_ERROR: ModuleNotFoundError: No module named '{m.group(1)}'>"
    return f"<IMPORT_ERROR: {type(e).__name__}: {msg}>"


def _is_package_owned(value, package_name: str) -> bool:
    """Return True iff this attribute is genuinely owned by the package
    (not a re-exported import or a stdlib name)."""
    import inspect

    # Modules themselves are not API symbols (they're navigation, not surface)
    if inspect.ismodule(value):
        return False
    # Functions / classes / data with __module__ outside the package are imports
    mod = getattr(value, "__module__", None)
    if isinstance(mod, str) and not (mod == package_name or mod.startswith(f"{package_name}.")):
        return False
    return True


def collect_symbols(package_name: str = "tokenpak") -> list[dict[str, str]]:
    """Walk a package and collect every public attribute that is GENUINELY
    package-owned (not a stdlib re-export, not a sub-module).

    If a module declares `__all__`, that list is the authoritative public surface
    for that module — use it verbatim (and trust the maintainer).

    Otherwise, filter `dir(mod)` to drop:
      - dunders / underscore-prefixed names
      - sub-modules (navigation, not surface)
      - names whose `__module__` is outside the package (re-exported imports)

    Import errors are recorded as `<IMPORT_ERROR: ...>` so they are visible.
    """
    symbols: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    try:
        pkg = importlib.import_module(package_name)
    except Exception as e:
        return [{"module": package_name, "name": _format_import_error(e, package_name)}]

    def harvest(mod_name: str, mod) -> None:
        explicit_all = getattr(mod, "__all__", None)
        if isinstance(explicit_all, (list, tuple)):
            for attr in explicit_all:
                if not isinstance(attr, str) or attr.startswith("_"):
                    continue
                key = (mod_name, attr)
                if key in _THIRD_PARTY_REEXPORTS:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                symbols.append({"module": mod_name, "name": attr})
            return
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            try:
                value = getattr(mod, attr)
            except Exception:
                continue
            if not _is_package_owned(value, package_name):
                continue
            key = (mod_name, attr)
            if key in _THIRD_PARTY_REEXPORTS:
                continue
            if key in seen:
                continue
            seen.add(key)
            symbols.append({"module": mod_name, "name": attr})

    # Top-level package
    harvest(package_name, pkg)

    if not hasattr(pkg, "__path__"):
        symbols.sort(key=lambda s: (s["module"], s["name"]))
        return symbols

    for finder, name, ispkg in pkgutil.walk_packages(pkg.__path__, prefix=f"{package_name}."):
        parts = name.split(".")
        if any(p.startswith("_") for p in parts):
            continue
        if "tests" in parts:
            continue
        # Dispatch is excluded from the released wheel (preview / main-only),
        # mirroring pyproject ``packages.find`` exclude of
        # ``tokenpak.orchestration.dispatch*``. The public-API snapshot records
        # the RELEASED package surface, so it likewise excludes the dispatch
        # subsystem (``orchestration.dispatch*``) and its CLI module
        # (``cli.commands.dispatch_cmd``) — it must not record preview/source-only
        # dispatch code as public released API.
        if name == "tokenpak.cli.commands.dispatch_cmd" or name.startswith(
            "tokenpak.orchestration.dispatch"
        ):
            continue
        try:
            mod = importlib.import_module(name)
        except Exception as e:
            symbols.append({"module": name, "name": _format_import_error(e, name)})
            continue
        harvest(name, mod)

    symbols.sort(key=lambda s: (s["module"], s["name"]))
    return symbols


def get_package_version() -> str:
    try:
        import tokenpak

        return getattr(tokenpak, "__version__", "unknown")
    except Exception:
        return "unknown"


def build_snapshot() -> dict:
    return {
        "version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "package_version": get_package_version(),
        "symbols": collect_symbols(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0] if __doc__ else "")
    parser.add_argument("--check", action="store_true", help="Exit 1 on diff")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    snapshot = build_snapshot()
    body = json.dumps(snapshot, indent=2, sort_keys=False) + "\n"

    args.out.parent.mkdir(parents=True, exist_ok=True)

    if args.check:
        if not args.out.exists():
            print(
                f"public-api.json missing at {args.out}; run `make api-snapshot` first",
                file=sys.stderr,
            )
            return 1
        on_disk = args.out.read_text()
        # Compare sets of symbols (ignore generated_at timestamp drift)
        try:
            on_disk_data = json.loads(on_disk)
        except Exception as e:
            print(f"on-disk snapshot is not valid JSON: {e}", file=sys.stderr)
            return 1
        on_disk_symbols = {(s["module"], s["name"]) for s in on_disk_data.get("symbols", [])}
        new_symbols = {(s["module"], s["name"]) for s in snapshot["symbols"]}
        added = sorted(new_symbols - on_disk_symbols)
        removed = sorted(on_disk_symbols - new_symbols)
        if added or removed:
            print("public-api snapshot drift detected:", file=sys.stderr)
            for m, n in added:
                print(f"  + {m}.{n}", file=sys.stderr)
            for m, n in removed:
                print(f"  - {m}.{n}", file=sys.stderr)
            print(
                "\nIf intentional: run `make api-snapshot` and commit the change", file=sys.stderr
            )
            print("plus a `.changeset/` entry. Removals also require a", file=sys.stderr)
            print("`removes-public-symbol:` line in the PR body per Std 21 §11.", file=sys.stderr)
            return 1
        print("public-api snapshot matches on-disk", file=sys.stderr)
        return 0

    args.out.write_text(body)
    print(
        f"public-api snapshot written: {args.out} ({len(snapshot['symbols'])} symbols)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
