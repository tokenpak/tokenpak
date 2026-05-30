# SPDX-License-Identifier: Apache-2.0
"""``tokenpak pak`` CLI subcommand (MultiPak Pro Phase 1, Beta 1).

Subcommands:
    create  <dir> --output       Package a directory into a Pak file (OSS)
    inspect <pak-id-or-file>     Show Pak metadata (read-only)
    export  <pak-id-or-file> -o  Extract Pak content + anchors to a directory
    import  <pak-file>           Install a Pak into the local store (OSS)
    status                       Diagnostic summary (always works)

Beta 1 OSS scope: ``create`` / ``import`` / ``export`` (file form) /
``inspect`` (file + ``pak:`` + ``vault:`` forms) round-trip in plain
JSON. Vault Paks are served by the OSS adapter. Encrypted Pak archives,
the capture pipeline, scoring, recall and PAKPlan-driven preview are
Pro features and route through the ``tokenpak-paid`` daemon.

Exit codes:
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

    p_create = paksub.add_parser(
        "create", help="Create a Pak file from a directory (OSS)",
        description=(
            "Package a directory into a Pak JSON file. The Pak captures "
            "anchor file content, objective/summary metadata, and a "
            "sha256 checksum. Encrypted Pak archives + capture pipeline "
            "are Pro features; plain JSON Paks are OSS Beta 1."
        ),
    )
    p_create.add_argument("source_dir", help="Directory to package")
    p_create.add_argument(
        "--output", "-o", required=True, help="Output Pak file path"
    )
    p_create.add_argument("--title", default="", help="Pak title (default: directory name)")
    p_create.add_argument("--objective", default="", help="Pak objective (free-form)")
    p_create.add_argument("--summary", default="", help="Pak summary (free-form)")
    p_create.add_argument("--ttl", default="", help="Pak TTL hint (free-form, e.g. '7d')")
    p_create.add_argument(
        "--continuation-notes", default="",
        help="Notes for continuation (free-form)",
    )
    p_create.add_argument(
        "--include-content", action="store_true", default=True,
        help="Embed file content in the Pak (default: on; use --no-include-content to omit)",
    )
    p_create.add_argument(
        "--no-include-content", dest="include_content", action="store_false",
        help="Omit file content; only record paths + per-file sha256",
    )
    p_create.add_argument(
        "--max-bytes", type=int, default=2_000_000,
        help="Skip files larger than this when embedding content (default: 2 MiB)",
    )
    p_create.set_defaults(func=cmd_pak_create)

    p_import = paksub.add_parser(
        "import", help="Install a Pak file into the local store (OSS)",
        description=(
            "Copy a Pak file into the local Pak store under "
            "<TOKENPAK_HOME>/paks/ so it is discoverable by `pak inspect <id>`. "
            "Pro daemon adds encryption-at-rest + capture pipeline; OSS "
            "import is a plain copy with checksum verification."
        ),
    )
    p_import.add_argument("pak_file", help="Path to a Pak file to install")
    p_import.add_argument(
        "--force", action="store_true",
        help="Overwrite if a Pak with the same id is already installed",
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
    from tokenpak import _paths
    from tokenpak.licensing.daemon_probe import detect_daemon_state

    state = detect_daemon_state()
    multipak_enabled = _read_multipak_enabled()
    pak_store_dir = _paths.under("pro", "state", "multipak")
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

    # OSS local store: resolve `pak:<short>` ids written by `pak create` /
    # installed by `pak import` into <TOKENPAK_HOME>/paks/.
    if pak_ref.startswith("pak:"):
        from tokenpak import _paths

        safe_id = pak_ref.replace(":", "_").replace("/", "_")
        candidate = _paths.under("paks") / f"{safe_id}.pak.json"
        if candidate.exists():
            return _inspect_from_file(str(candidate), as_json=as_json)
        msg = f"pak not installed: {pak_ref} (looked under {candidate})"
        if as_json:
            print(json.dumps({"error": "pak_not_found", "detail": msg}))
        else:
            print(f"✗ tokenpak pak inspect — {msg}", file=sys.stderr)
        return 1

    # Daemon-required subtypes (interaction:, decision:, recall:, handoff:)
    return _emit_pro_required(
        f"Pak {pak_ref!r} requires the Pro daemon — non-Vault subtypes are "
        "encrypted at rest in <TOKENPAK_HOME>/pro/state/multipak/.",
        as_json=as_json,
    )


def cmd_pak_export(args: Any) -> int:
    """Export a Pak to a directory.

    Three forms supported in Beta 1:
      - ``vault:<block-id>`` — Vault Pak (read-only, no anchor content)
      - ``pak:<short>`` — Pak installed in the local store via ``pak import``
      - ``<path>`` — file-form Pak on disk (.pak.json or arbitrary path)

    The Pro daemon adds encrypted-Pak export; OSS handles plain forms.
    """
    pak_ref: str = args.pak_ref
    output: str = args.output

    out_dir = Path(output)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"✗ tokenpak pak export — cannot create output directory: {exc}",
            file=sys.stderr,
        )
        return 1

    # Vault form
    if pak_ref.startswith("vault:"):
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

    # Local-store id form → resolve to file
    if pak_ref.startswith("pak:"):
        from tokenpak import _paths

        safe_id = pak_ref.replace(":", "_").replace("/", "_")
        candidate = _paths.under("paks") / f"{safe_id}.pak.json"
        if not candidate.exists():
            print(
                f"✗ tokenpak pak export — pak not installed: {pak_ref}",
                file=sys.stderr,
            )
            return 1
        return _export_file_pak(str(candidate), out_dir)

    # Path form — file-on-disk Pak
    if "/" in pak_ref or pak_ref.endswith(".pak") or pak_ref.endswith(".json"):
        return _export_file_pak(pak_ref, out_dir)

    return _emit_pro_required(
        f"Exporting Pak {pak_ref!r} requires the Pro daemon.",
        as_json=False,
    )


def _export_file_pak(path: str, out_dir: Path) -> int:
    """Write a file-form Pak's anchors back to ``out_dir``.

    Embedded utf-8 content is restored verbatim; base64 anchors are
    decoded to bytes; reference-only anchors (no ``content`` field) are
    listed but skipped with a notice.
    """
    p = Path(path)
    if not p.exists():
        print(f"✗ tokenpak pak export — file not found: {path}", file=sys.stderr)
        return 1
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ tokenpak pak export — cannot parse Pak file: {exc}", file=sys.stderr)
        return 1

    pak_json = out_dir / "pak.json"
    pak_json.write_text(json.dumps(payload, indent=2))
    anchors = payload.get("anchors") or []
    written = 0
    skipped = 0
    for anchor in anchors:
        rel = anchor.get("path")
        content = anchor.get("content")
        encoding = anchor.get("encoding", "utf-8")
        if not rel or content is None:
            skipped += 1
            continue
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if encoding == "base64":
                import base64

                target.write_bytes(base64.b64decode(content))
            else:
                target.write_text(content, encoding="utf-8")
            written += 1
        except OSError as exc:
            print(
                f"⚠️  Could not write {rel}: {exc}",
                file=sys.stderr,
            )
            skipped += 1
    print(f"✅ Exported Pak {payload.get('pak_id', '?')} → {out_dir}")
    print(f"   files written: {written}  skipped: {skipped}  metadata: {pak_json}")
    return 0


def cmd_pak_create(args: Any) -> int:
    """Package a directory into a Pak JSON file (OSS Beta 1).

    The Pak file is JSON with embedded anchor content (when small enough)
    or path+sha256 references (when --no-include-content or oversized).
    Pro encryption-at-rest + capture pipeline are additive; plain JSON
    is the OSS substrate.
    """
    import datetime
    import hashlib

    src = Path(args.source_dir).expanduser()
    out = Path(args.output).expanduser()

    if not src.exists() or not src.is_dir():
        print(
            f"✗ tokenpak pak create — source directory not found: {src}",
            file=sys.stderr,
        )
        return 1

    title = args.title or src.name
    include_content: bool = bool(getattr(args, "include_content", True))
    max_bytes: int = int(getattr(args, "max_bytes", 2_000_000))

    anchors: list[dict] = []
    skipped: list[dict] = []
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(src).parts):
            continue
        rel = str(path.relative_to(src))
        try:
            data = path.read_bytes()
        except OSError as exc:
            skipped.append({"path": rel, "reason": f"read_error: {exc}"})
            continue
        sha = hashlib.sha256(data).hexdigest()
        anchor: dict[str, Any] = {
            "path": rel,
            "sha256": sha,
            "bytes": len(data),
        }
        if include_content and len(data) <= max_bytes:
            try:
                anchor["content"] = data.decode("utf-8")
                anchor["encoding"] = "utf-8"
            except UnicodeDecodeError:
                import base64

                anchor["content"] = base64.b64encode(data).decode("ascii")
                anchor["encoding"] = "base64"
        elif include_content:
            skipped.append({"path": rel, "reason": f"oversized: {len(data)}>{max_bytes}"})
        anchors.append(anchor)

    created_at = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    pak_payload: dict[str, Any] = {
        "schema_version": 1,
        "pak_type": "context",
        "title": title,
        "objective": args.objective,
        "summary": args.summary,
        "ttl": args.ttl,
        "continuation_notes": getattr(args, "continuation_notes", ""),
        "created_at": created_at,
        "scope": {"source_root": str(src)},
        "anchors": anchors,
        "skipped": skipped,
        "token_estimate": _estimate_tokens(anchors),
    }
    body_for_hash = json.dumps(
        {k: v for k, v in pak_payload.items() if k != "checksum"},
        sort_keys=True,
    ).encode("utf-8")
    pak_payload["checksum"] = "sha256:" + hashlib.sha256(body_for_hash).hexdigest()
    pak_id = "pak:" + pak_payload["checksum"][len("sha256:") : len("sha256:") + 16]
    pak_payload["pak_id"] = pak_id

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(pak_payload, indent=2))
    except OSError as exc:
        print(
            f"✗ tokenpak pak create — cannot write output: {exc}",
            file=sys.stderr,
        )
        return 1

    print(f"✅ Created Pak {pak_id} → {out}")
    print(f"   anchors: {len(anchors)}  skipped: {len(skipped)}  "
          f"checksum: {pak_payload['checksum'][:24]}…")
    if skipped:
        print(f"ℹ️  {len(skipped)} file(s) skipped — see Pak 'skipped' field for details.")
    return 0


def cmd_pak_import(args: Any) -> int:
    """Install a Pak file into the local store (OSS Beta 1).

    Verifies the Pak's checksum, copies the file to
    ``<TOKENPAK_HOME>/paks/<pak_id>.pak.json``, and registers it for
    discovery by ``pak inspect <pak_id>``. Pro daemon would add
    encryption-at-rest + capture-pipeline ingest; OSS does the plain
    copy.
    """
    import hashlib
    import shutil

    from tokenpak import _paths

    src = Path(args.pak_file).expanduser()
    if not src.exists() or not src.is_file():
        print(
            f"✗ tokenpak pak import — file not found: {src}",
            file=sys.stderr,
        )
        return 1

    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"✗ tokenpak pak import — cannot parse Pak file: {exc}",
            file=sys.stderr,
        )
        return 1

    if not isinstance(payload, dict):
        print(
            "✗ tokenpak pak import — Pak file is not a JSON object",
            file=sys.stderr,
        )
        return 1

    declared = payload.get("checksum", "")
    body_for_hash = json.dumps(
        {k: v for k, v in payload.items() if k not in ("checksum", "pak_id")},
        sort_keys=True,
    ).encode("utf-8")
    actual = "sha256:" + hashlib.sha256(body_for_hash).hexdigest()
    if declared and declared != actual:
        print(
            f"✗ tokenpak pak import — checksum mismatch (declared {declared[:20]}…, "
            f"computed {actual[:20]}…)",
            file=sys.stderr,
        )
        return 1

    pak_id = payload.get("pak_id") or (
        "pak:" + actual[len("sha256:") : len("sha256:") + 16]
    )
    store_dir = _paths.under("paks")
    store_dir.mkdir(parents=True, exist_ok=True)
    safe_id = pak_id.replace(":", "_").replace("/", "_")
    target = store_dir / f"{safe_id}.pak.json"

    if target.exists() and not getattr(args, "force", False):
        print(
            f"✗ tokenpak pak import — already installed: {target} "
            "(use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    shutil.copyfile(src, target)
    print(f"✅ Imported Pak {pak_id} → {target}")
    print(f"   checksum verified: {actual[:24]}…")
    print(f"   inspect with: tokenpak pak inspect {target}")
    return 0


def _estimate_tokens(anchors: list[dict]) -> int:
    """Rough token estimate (chars / 4) over embedded anchor content.

    Beta 1 placeholder — Pro adds real model-specific tokenizers.
    """
    total_chars = 0
    for a in anchors:
        c = a.get("content")
        if isinstance(c, str):
            total_chars += len(c)
    return total_chars // 4


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
    """Best-effort vault index block count for `pak status`.

    Tester contract: ``pak status`` must NEVER trigger a heavy vault
    index load. The vault subsystem has its own ``tokenpak vault
    status`` verb for that. We only report a count when the proxy
    module is already loaded in this process AND the index is in
    memory; otherwise we return 0 with the understanding that the
    text/JSON output explains the user has to run ``tokenpak vault
    status`` for the real count.
    """
    import sys as _sys

    if "tokenpak.proxy.vault_bridge" not in _sys.modules:
        return 0
    try:
        from tokenpak.proxy.vault_bridge import get_vault_index  # type: ignore[import]

        vi = get_vault_index()
        if vi is None:
            return 0
        # Some implementations build the index lazily on call — only
        # consult an already-realised ``blocks`` mapping; never trigger
        # population from this status path.
        blocks = getattr(vi, "blocks", None)
        if not isinstance(blocks, dict):
            return 0
        return len(blocks)
    except Exception:
        return 0


def _promotion_candidate_count() -> int:
    """Count of journal entries marked as Pak promotion candidates."""
    from tokenpak import _paths

    db_path = _paths.under("companion", "journal.db")
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
    """Render a Pak's metadata.

    Handles two shapes:
      - Beta 1 OSS file form (``schema_version: 1``, anchors with embedded
        content, top-level objective/summary/checksum) produced by
        ``pak create``.
      - Vault Pak form (``status``/``authority``/``confidence``/``source``
        sub-objects) produced by the Vault adapter.
    """
    print(f"Pak {payload.get('pak_id', '?')}")
    print("─" * 40)
    print(f"  type        : {payload.get('pak_type', '?')}")
    print(f"  title       : {payload.get('title', '')}")

    # Beta 1 file form
    if payload.get("schema_version") is not None:
        if payload.get("objective"):
            print(f"  objective   : {payload['objective']}")
        if payload.get("ttl"):
            print(f"  ttl         : {payload['ttl']}")
        if payload.get("token_estimate") is not None:
            print(f"  tokens (est): {payload['token_estimate']}")
        anchors = payload.get("anchors") or []
        print(f"  anchors     : {len(anchors)}")
        if payload.get("checksum"):
            print(f"  checksum    : {payload['checksum'][:32]}…")
        if payload.get("created_at"):
            print(f"  created_at  : {payload['created_at']}")
        scope = payload.get("scope", {}) or {}
        if scope.get("source_root"):
            print(f"  source_root : {scope['source_root']}")
        if payload.get("summary"):
            print()
            print("Summary:")
            print(f"  {payload['summary']}")
        if payload.get("continuation_notes"):
            print()
            print("Continuation notes:")
            print(f"  {payload['continuation_notes']}")
        return

    # Vault Pak form
    print(f"  status      : {payload.get('status', '?')}")
    print(f"  authority   : {payload.get('authority', '?')}")
    print(f"  confidence  : {payload.get('confidence', '?')}")
    src = payload.get("source", {}) or {}
    print(f"  source      : {src.get('platform', '?')} ({src.get('source_type', '?')})")
    src_hash = src.get('source_hash', '') or ''
    print(f"  source_hash : {src_hash[:16]}…" if src_hash else "  source_hash : ")
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

    Exit code 1 (user-facing error). Mirrors the
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
