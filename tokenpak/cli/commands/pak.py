# SPDX-License-Identifier: Apache-2.0
"""``tokenpak pak`` CLI subcommand (Std 32 §1.3 row 5, Phase 1).

Subcommands:
    inspect <pak-id-or-file>     Show Pak metadata (read-only)
    export  <pak-id> --output    Extract Pak content + anchors to a directory
    import  <dir> --output       Package a directory into a Pak file
    status                       Diagnostic summary (always works)

Phase 1 scope: Vault Paks are fully served by the OSS adapter; other
subtypes require the Pro daemon (returns "not Pro-installed" message
with exit code 1). The status action always works regardless of
multipak.enabled or daemon presence.

Exit codes (Std 03 §1):
    0  success
    1  user-facing error (missing file, daemon required for action, etc.)
    2  argparse usage error (handled by argparse itself)
    4  config error (unused in Phase 1; reserved)
    5  internal error (uncaught exception in handler)

JSON output: --json on inspect + status emits the exact same payload
shapes as the corresponding /pak/v1/* HTTP endpoints — by design, so
fleet automation and dashboards see one canonical shape.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Tokenpak imports are deferred into handlers to keep `tokenpak --help`
# fast (these contracts pull in the vault subsystem on import).


# ---------------------------------------------------------------------------
# Argparse builder — wired into _cli_core.build_parser via _build_pak_parser
# ---------------------------------------------------------------------------


def build_pak_parser(sub: Any) -> None:
    """Register the ``tokenpak pak`` subcommand and its actions on ``sub``.

    Called from :func:`tokenpak._cli_core.build_parser`. ``sub`` is the
    subparsers object returned by ``parser.add_subparsers(...)``.
    """
    p_pak = sub.add_parser(
        "pak",
        help="Inspect, export, import Pak files (MultiPak Pro Phase 1)",
        description=(
            "MultiPak Pro Phase 1 OSS surface. Read-only Vault Pak "
            "operations work without Pro; other Pak subtypes require the "
            "tokenpak-paid daemon."
        ),
    )
    paksub = p_pak.add_subparsers(dest="pak_action", required=False)

    p_inspect = paksub.add_parser(
        "inspect", help="Show Pak metadata (read-only)"
    )
    p_inspect.add_argument(
        "pak_ref",
        help="Pak ID (e.g. 'vault:path#hash') or path to a Pak file",
    )
    p_inspect.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text"
    )
    p_inspect.set_defaults(func=cmd_pak_inspect)

    p_export = paksub.add_parser(
        "export", help="Extract Pak content + anchors to a directory"
    )
    p_export.add_argument("pak_ref", help="Pak ID to export")
    p_export.add_argument(
        "--output", "-o", required=True, help="Output directory"
    )
    p_export.set_defaults(func=cmd_pak_export)

    p_import = paksub.add_parser(
        "import", help="Package a directory into a Pak file"
    )
    p_import.add_argument("source_dir", help="Directory to package")
    p_import.add_argument(
        "--output", "-o", required=True, help="Output Pak file path"
    )
    p_import.set_defaults(func=cmd_pak_import)

    p_status = paksub.add_parser(
        "status", help="Show MultiPak Pro readiness diagnostics"
    )
    p_status.add_argument(
        "--json", action="store_true", help="Emit JSON instead of text"
    )
    p_status.set_defaults(func=cmd_pak_status)

    # Default — bare `tokenpak pak` prints help.
    p_pak.set_defaults(func=lambda a: p_pak.print_help())


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_pak_status(args: Any) -> int:
    """Emit the Pro-readiness diagnostic summary.

    Mirrors the GET /pak/v1/status payload — same field names, same
    types. Always exits 0 (status is informational, not pass/fail).
    """
    from tokenpak.licensing.daemon_probe import detect_daemon_state

    state = detect_daemon_state()
    multipak_enabled = _read_multipak_enabled()
    pak_store_dir = Path.home() / ".tokenpak" / "pro" / "state" / "multipak"
    pak_store_present = pak_store_dir.is_dir()
    vault_paks_indexed = _vault_block_count()
    promotion_candidates = _promotion_candidate_count()

    payload = {
        "daemon_state": state,
        "multipak_enabled": multipak_enabled,
        "pak_store_present": pak_store_present,
        "vault_paks_indexed": vault_paks_indexed,
        "promotion_candidates": promotion_candidates,
    }

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return 0

    # Text rendering — uses the same emoji conventions as `tokenpak doctor`
    # (✅ ready, ⚠️ partial, ❌ unavailable).
    print("MultiPak Pro Phase 1 status")
    print("───────────────────────────")
    daemon_icon = "✅" if state == "active" else "❌"
    print(f"{daemon_icon} Daemon state           : {state}")
    enabled_icon = "✅" if multipak_enabled else "⚠️"
    print(f"{enabled_icon} multipak.enabled       : {multipak_enabled}")
    store_icon = "✅" if pak_store_present else "⚠️"
    print(f"{store_icon} Pak store present      : {pak_store_present}")
    print(f"📦 Vault Paks indexed     : {vault_paks_indexed}")
    print(f"📦 Promotion candidates   : {promotion_candidates}")
    if state == "unavailable":
        print()
        print(
            "ℹ️  Pro daemon not installed — Vault Pak inspection still works "
            "via the OSS adapter. Install tokenpak-paid for the full surface."
        )
    return 0


def cmd_pak_inspect(args: Any) -> int:
    """Inspect a Pak by ID or file path.

    Vault Paks (``vault:<block-id>``) are served by the OSS adapter.
    Other subtypes require the daemon — Phase 1 returns a clear error.
    """
    pak_ref: str = args.pak_ref
    as_json: bool = getattr(args, "json", False)

    # Path form: read Pak from disk (JSON file).
    if "/" in pak_ref or pak_ref.endswith(".pak") or pak_ref.endswith(".json"):
        return _inspect_from_file(pak_ref, as_json=as_json)

    # ID form: dispatch by prefix.
    if pak_ref.startswith("vault:"):
        return _inspect_vault_id(pak_ref, as_json=as_json)

    # Daemon-required subtypes (interaction:, decision:, recall:, handoff:)
    return _emit_pro_required(
        f"Pak {pak_ref!r} requires the Pro daemon — non-Vault subtypes are "
        "encrypted at rest in ~/.tokenpak/pro/state/multipak/.",
        as_json=as_json,
    )


def cmd_pak_export(args: Any) -> int:
    """Export a Pak to a directory.

    Phase 1: only Vault Paks supported (read-only, no encryption to undo).
    Other subtypes require the daemon.
    """
    pak_ref: str = args.pak_ref
    output: str = args.output

    if not pak_ref.startswith("vault:"):
        return _emit_pro_required(
            f"Exporting Pak {pak_ref!r} requires the Pro daemon.",
            as_json=False,
        )

    out_dir = Path(output)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"✗ tokenpak pak export — cannot create output directory: {exc}",
            file=sys.stderr,
        )
        return 1

    # Resolve the Pak via the OSS adapter, write metadata as Pak JSON.
    pak = _resolve_vault_pak(pak_ref)
    if pak is None:
        print(
            f"✗ tokenpak pak export — vault block not indexed: {pak_ref}",
            file=sys.stderr,
        )
        return 1

    pak_json_path = out_dir / "pak.json"
    pak_json_path.write_text(json.dumps(pak.to_dict(), indent=2))
    print(f"✅ Exported Vault Pak → {pak_json_path}")
    print(
        "ℹ️  Anchors not included (Vault Paks reference source files directly; "
        "use `tokenpak vault block <id>` to fetch source content)."
    )
    return 0


def cmd_pak_import(args: Any) -> int:
    """Phase 1: full Pak packaging requires the daemon's encryption layer.

    The OSS surface admits a thin pass — we render the directory as a
    Vault-Pak-shaped JSON file as a debugging convenience. Real Pak
    creation goes through the daemon.
    """
    return _emit_pro_required(
        "Pak import requires the Pro daemon (capture pipeline, encryption "
        "at rest). Phase 1 OSS does not ship a packaging path.",
        as_json=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_multipak_enabled() -> bool:
    """Mirrors :func:`tokenpak.proxy.app_endpoints._read_multipak_enabled`.

    Inlined here (small surface, low risk of drift) rather than imported
    to avoid pulling the proxy module graph for `tokenpak pak status`.
    """
    try:
        from tokenpak.core.config_loader import load_config
    except ImportError:
        return False
    try:
        cfg = load_config()
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False
    pro = cfg.get("pro")
    if isinstance(pro, dict):
        mp = pro.get("multipak")
        if isinstance(mp, dict):
            v = mp.get("enabled")
            if isinstance(v, bool):
                return v
    mp = cfg.get("multipak")
    if isinstance(mp, dict):
        v = mp.get("enabled")
        if isinstance(v, bool):
            return v
    return False


def _vault_block_count() -> int:
    """Best-effort vault index block count for `pak status`."""
    try:
        from tokenpak.proxy.vault_bridge import get_vault_index

        vi = get_vault_index()
        if vi is None:
            return 0
        return len(getattr(vi, "blocks", {}) or {})
    except Exception:
        return 0


def _promotion_candidate_count() -> int:
    """Count of journal entries marked as Pak promotion candidates."""
    db_path = Path.home() / ".tokenpak" / "companion" / "journal.db"
    if not db_path.exists():
        return 0
    try:
        from tokenpak.companion.journal.pak_aware import count_promotion_candidates

        return count_promotion_candidates(db_path)
    except Exception:
        return 0


def _resolve_vault_pak(pak_ref: str):
    """Return a Pak instance for a vault: ID, or None when not indexed."""
    block_id = pak_ref[len("vault:"):]
    try:
        from tokenpak.proxy.vault_bridge import get_vault_index

        vi = get_vault_index()
        if vi is None:
            return None
        blocks = getattr(vi, "blocks", None) or {}
        block = blocks.get(block_id)
        if block is None:
            return None
        from tokenpak.vault.pak_adapter import vault_block_to_pak

        return vault_block_to_pak(block)
    except Exception:
        return None


def _inspect_vault_id(pak_ref: str, *, as_json: bool) -> int:
    pak = _resolve_vault_pak(pak_ref)
    if pak is None:
        msg = f"vault block not indexed: {pak_ref}"
        if as_json:
            print(json.dumps({"error": "pak_not_found", "detail": msg}))
        else:
            print(f"✗ tokenpak pak inspect — {msg}", file=sys.stderr)
        return 1
    payload = pak.to_dict()
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        _print_pak_text(payload)
    return 0


def _inspect_from_file(path: str, *, as_json: bool) -> int:
    p = Path(path)
    if not p.exists():
        msg = f"file not found: {path}"
        if as_json:
            print(json.dumps({"error": "file_not_found", "detail": msg}))
        else:
            print(f"✗ tokenpak pak inspect — {msg}", file=sys.stderr)
        return 1
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"cannot parse Pak file: {exc}"
        if as_json:
            print(json.dumps({"error": "invalid_pak_file", "detail": msg}))
        else:
            print(f"✗ tokenpak pak inspect — {msg}", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        _print_pak_text(data)
    return 0


def _print_pak_text(payload: dict) -> None:
    print(f"Pak {payload.get('pak_id', '?')}")
    print("─" * 40)
    print(f"  type        : {payload.get('pak_type', '?')}")
    print(f"  title       : {payload.get('title', '')}")
    print(f"  status      : {payload.get('status', '?')}")
    print(f"  authority   : {payload.get('authority', '?')}")
    print(f"  confidence  : {payload.get('confidence', '?')}")
    src = payload.get("source", {}) or {}
    print(f"  source      : {src.get('platform', '?')} ({src.get('source_type', '?')})")
    print(f"  source_hash : {src.get('source_hash', '')[:16]}…")
    print(f"  created_at  : {src.get('created_at', '?')}")
    scope = payload.get("scope", {}) or {}
    if scope.get("project"):
        print(f"  project     : {scope['project']}")
    summary = payload.get("summary", "")
    if summary:
        print()
        print("Summary:")
        print(f"  {summary}")


def _emit_pro_required(detail: str, *, as_json: bool) -> int:
    """Standardized "this needs the Pro daemon" error response.

    Exit code 1 (user-facing error per Std 03 §1). Mirrors the
    /pak/v1/* 501 envelope so machine consumers see one shape.
    """
    payload = {
        "error": "not_implemented",
        "reason": "pro_daemon_required",
        "detail": detail,
        "suggested_action": "Install tokenpak-paid (Pro) to enable this surface.",
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"✗ tokenpak pak — {detail}", file=sys.stderr)
        print(
            "  Install tokenpak-paid (Pro) to enable this surface.",
            file=sys.stderr,
        )
    return 1


__all__ = [
    "build_pak_parser",
    "cmd_pak_export",
    "cmd_pak_import",
    "cmd_pak_inspect",
    "cmd_pak_status",
]
