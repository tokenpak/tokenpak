# SPDX-License-Identifier: Apache-2.0
"""``tokenpak pak`` CLI subcommand (MultiPak Pro Phase 1, Beta 1).

Subcommands:
    create  <dir> --output       Package a directory into a Pak file (OSS)
    inspect <pak-id-or-file>     Show Pak metadata (read-only)
    export  <pak-id-or-file> -o  Extract Pak content + anchors to a directory
    import  <pak-file>           Install a Pak into the local store (OSS)
    migrate <pak-file> [-o]      Upgrade a legacy Pak file to the canonical schema
    status                       Diagnostic summary (always works)

Beta 1 OSS scope: ``create`` / ``import`` / ``export`` (file form) /
``inspect`` (file + ``pak:`` + ``vault:`` forms) round-trip in plain
JSON. Vault Paks are served by the OSS adapter. Encrypted Pak archives,
the capture pipeline, scoring, recall and PAKPlan-driven preview are
Pro features and route through the ``tokenpak-paid`` daemon.

File-form schema: ``pak create`` writes ``schema_version: 2`` — the
canonical Pak wire contract (``tokenpak.tip.pak.Pak``) extended with
file-form fields (embedded anchor content, ``objective``, ``ttl_hint``,
``continuation_notes``, ``source_root``, ``token_estimate``, ``skipped``,
``checksum``). Legacy ``schema_version: 1`` files (which carried the
deprecated ``context`` subtype alias) remain fully readable by
``inspect`` / ``import`` / ``export`` and can be upgraded in place with
``pak migrate``.

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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tokenpak.tip.pak import Pak

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

    p_inspect = paksub.add_parser("inspect", help="Show Pak metadata (read-only)")
    p_inspect.add_argument(
        "pak_ref",
        help="Pak ID (e.g. 'vault:path#hash') or path to a Pak file",
    )
    p_inspect.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    p_inspect.set_defaults(func=cmd_pak_inspect)

    p_export = paksub.add_parser("export", help="Extract Pak content + anchors to a directory")
    p_export.add_argument("pak_ref", help="Pak ID to export")
    p_export.add_argument("--output", "-o", required=True, help="Output directory")
    p_export.set_defaults(func=cmd_pak_export)

    p_create = paksub.add_parser(
        "create",
        help="Create a Pak file from a directory (OSS)",
        description=(
            "Package a directory into a Pak JSON file. The Pak captures "
            "anchor file content, objective/summary metadata, and a "
            "sha256 checksum. Encrypted Pak archives + capture pipeline "
            "are Pro features; plain JSON Paks are OSS Beta 1."
        ),
    )
    p_create.add_argument("source_dir", help="Directory to package")
    p_create.add_argument("--output", "-o", required=True, help="Output Pak file path")
    p_create.add_argument("--title", default="", help="Pak title (default: directory name)")
    p_create.add_argument("--objective", default="", help="Pak objective (free-form)")
    p_create.add_argument("--summary", default="", help="Pak summary (free-form)")
    p_create.add_argument("--ttl", default="", help="Pak TTL hint (free-form, e.g. '7d')")
    p_create.add_argument(
        "--continuation-notes",
        default="",
        help="Notes for continuation (free-form)",
    )
    p_create.add_argument(
        "--include-content",
        action="store_true",
        default=True,
        help="Embed file content in the Pak (default: on; use --no-include-content to omit)",
    )
    p_create.add_argument(
        "--no-include-content",
        dest="include_content",
        action="store_false",
        help="Omit file content; only record paths + per-file sha256",
    )
    p_create.add_argument(
        "--max-bytes",
        type=int,
        default=2_000_000,
        help="Skip files larger than this when embedding content (default: 2 MiB)",
    )
    p_create.set_defaults(func=cmd_pak_create)

    p_import = paksub.add_parser(
        "import",
        help="Install a Pak file into the local store (OSS)",
        description=(
            "Copy a Pak file into the local Pak store under "
            "<TOKENPAK_HOME>/paks/ so it is discoverable by `pak inspect <id>`. "
            "Pro daemon adds encryption-at-rest + capture pipeline; OSS "
            "import is a plain copy with checksum verification."
        ),
    )
    p_import.add_argument("pak_file", help="Path to a Pak file to install")
    p_import.add_argument(
        "--force",
        action="store_true",
        help="Overwrite if a Pak with the same id is already installed",
    )
    p_import.set_defaults(func=cmd_pak_import)

    p_migrate = paksub.add_parser(
        "migrate",
        help="Upgrade a legacy Pak file to the canonical schema (OSS)",
        description=(
            "Rewrite a legacy (schema_version 1) Pak file in the canonical "
            "schema_version 2 form: deprecated subtype aliases resolve to "
            "their canonical names, anchors gain content-hash fields, and "
            "the checksum is recomputed. The pak_id is preserved and anchor "
            "content is unchanged. Files already in canonical form are left "
            "untouched."
        ),
    )
    p_migrate.add_argument("pak_file", help="Path to a Pak file to migrate")
    p_migrate.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write the migrated Pak here (default: rewrite the file in place)",
    )
    p_migrate.set_defaults(func=cmd_pak_migrate)

    p_status = paksub.add_parser("status", help="Show MultiPak Pro readiness diagnostics")
    p_status.add_argument("--json", action="store_true", help="Emit JSON instead of text")
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


def _within_dir(base: Path, rel: str) -> bool:
    """True iff ``base / rel`` stays inside ``base`` after resolution.

    Pak anchor paths are untrusted input. An absolute ``rel`` (``/etc/x``)
    or one that climbs out (``../x``) must never write outside the chosen
    export directory. Reject absolute paths pre-join, then resolve and
    confirm containment (``is_relative_to`` is available on the 3.10 floor).
    """
    if Path(rel).is_absolute():
        return False
    base_resolved = base.resolve()
    return (base_resolved / rel).resolve().is_relative_to(base_resolved)


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
        # A1 (codex-review-1): contain writes within out_dir — reject an
        # absolute or ``..``-traversing anchor path before it can escape.
        if not _within_dir(out_dir, rel):
            print(
                f"⚠️  Skipped unsafe anchor path (escapes export dir): {rel}",
                file=sys.stderr,
            )
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
    or path+hash references (when --no-include-content or oversized).
    Pro encryption-at-rest + capture pipeline are additive; plain JSON
    is the OSS substrate.

    Emits ``schema_version: 2``: the canonical Pak wire contract (built
    through :class:`tokenpak.tip.pak.Pak` so the shape cannot drift from
    the contract) plus the file-form extension fields. The subtype is the
    canonical ``recall`` — the ruled resolution of the retired ``context``
    alias — never a deprecated name.
    """
    import hashlib

    from tokenpak.tip.pak import (
        Pak,
        PakAuthority,
        PakConfidence,
        PakRetentionPolicy,
        PakScope,
        PakSource,
        PakSourceType,
        PakStatus,
        PakSubtype,
        default_retention_for,
    )

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

    anchors: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for path in sorted(src.rglob("*")):
        # A2 (codex-review-1): never follow symlinks — a link to e.g.
        # /etc/hostname would otherwise be read as a file and its target
        # content embedded into the Pak. Skip before the is_file() probe.
        if path.is_symlink():
            continue
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
        # Canonical anchor record (anchor_id / source_hash / snippet_available
        # per the Pak contract) extended with the file-form fields that make
        # the payload self-contained (path / bytes / content / encoding).
        anchor: dict[str, Any] = {
            "anchor_id": sha[:16],
            "source_hash": sha,
            "snippet_available": False,
            "path": rel,
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
            anchor["snippet_available"] = True
        elif include_content:
            skipped.append({"path": rel, "reason": f"oversized: {len(data)}>{max_bytes}"})
        anchors.append(anchor)

    created_at = _utc_now_iso()
    # The retired file-form subtype ("context") is a deprecated alias whose
    # canonical resolution is "recall" — emit the canonical name so created
    # Paks never carry a value the contract is migrating away from.
    subtype = PakSubtype.RECALL
    core = Pak(
        pak_id="pak:unassigned",  # replaced below once the checksum exists
        pak_type=subtype,
        title=title,
        summary=args.summary or "",
        scope=PakScope(),
        source=PakSource(
            platform="tokenpak-cli",
            source_type=PakSourceType.FILE,
            created_at=created_at,
            source_hash=_aggregate_source_hash(anchors),
        ),
        status=PakStatus.PROPOSED,
        authority=PakAuthority.FILE_SOURCE,
        confidence=PakConfidence.HIGH,
        retention=PakRetentionPolicy(ttl=default_retention_for(subtype)),
    )
    pak_payload: dict[str, Any] = core.to_dict()
    del pak_payload["pak_id"]
    pak_payload["anchors"] = anchors
    pak_payload.update(
        {
            "schema_version": 2,
            "objective": args.objective,
            "ttl_hint": args.ttl,
            "continuation_notes": getattr(args, "continuation_notes", ""),
            "source_root": str(src),
            "skipped": skipped,
            "token_estimate": _estimate_tokens(anchors),
        }
    )
    pak_payload["checksum"] = _compute_checksum(pak_payload)
    pak_id = _derive_pak_id(pak_payload["checksum"])
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
    print(
        f"   anchors: {len(anchors)}  skipped: {len(skipped)}  "
        f"checksum: {pak_payload['checksum'][:24]}…"
    )
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
    actual = _compute_checksum(payload)
    if declared and declared != actual:
        print(
            f"✗ tokenpak pak import — checksum mismatch (declared {declared[:20]}…, "
            f"computed {actual[:20]}…)",
            file=sys.stderr,
        )
        return 1

    pak_id = payload.get("pak_id") or _derive_pak_id(actual)
    store_dir = _paths.write_under("paks")
    store_dir.mkdir(parents=True, exist_ok=True)
    safe_id = pak_id.replace(":", "_").replace("/", "_")
    target = store_dir / f"{safe_id}.pak.json"

    if target.exists() and not getattr(args, "force", False):
        print(
            f"✗ tokenpak pak import — already installed: {target} (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    shutil.copyfile(src, target)
    print(f"✅ Imported Pak {pak_id} → {target}")
    # A5 (codex-review-1): only claim "verified" when there was a declared
    # checksum to verify against (a mismatch already returned above). With no
    # declared checksum nothing was verified — say so rather than imply trust.
    if declared:
        print(f"   checksum verified: {actual[:24]}…")
    else:
        print(
            "   checksum computed (Pak carries no declared checksum to "
            f"verify against): {actual[:24]}…"
        )
    _, is_legacy_form = upgrade_pak_payload(payload)
    if is_legacy_form:
        print(f"ℹ️  Legacy Pak schema — upgrade the source file with: tokenpak pak migrate {src}")
    print(f"   inspect with: tokenpak pak inspect {target}")
    return 0


def cmd_pak_migrate(args: Any) -> int:
    """Upgrade a legacy Pak file to the canonical schema (schema_version 2).

    Integrity-gated: when the file declares a checksum it is verified
    BEFORE migration, so a tampered file cannot be laundered into a fresh
    valid checksum. The original ``pak_id`` is preserved (store identity
    survives migration); the checksum is recomputed over the migrated
    body. Anchor content is carried over unchanged. Files already in
    canonical form are left untouched.
    """
    src = Path(args.pak_file).expanduser()
    if not src.exists() or not src.is_file():
        print(f"✗ tokenpak pak migrate — file not found: {src}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ tokenpak pak migrate — cannot parse Pak file: {exc}", file=sys.stderr)
        return 1

    if not isinstance(payload, dict):
        print("✗ tokenpak pak migrate — Pak file is not a JSON object", file=sys.stderr)
        return 1

    declared = payload.get("checksum", "")
    if declared:
        actual = _compute_checksum(payload)
        if declared != actual:
            print(
                f"✗ tokenpak pak migrate — checksum mismatch (declared {declared[:20]}…, "
                f"computed {actual[:20]}…); refusing to migrate a tampered file",
                file=sys.stderr,
            )
            return 1

    upgraded, changed = upgrade_pak_payload(payload)
    out = Path(args.output).expanduser() if getattr(args, "output", None) else src

    if not changed:
        if out != src:
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(upgraded, indent=2))
            except OSError as exc:
                print(
                    f"✗ tokenpak pak migrate — cannot write output: {exc}",
                    file=sys.stderr,
                )
                return 1
        print(f"✅ Pak already in canonical form — nothing to migrate: {src}")
        return 0

    new_checksum = _compute_checksum(upgraded)
    upgraded["checksum"] = new_checksum
    pak_id = payload.get("pak_id") or _derive_pak_id(new_checksum)
    upgraded["pak_id"] = pak_id

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-replace so an interrupted migration never leaves a
        # half-written Pak where a valid one used to be.
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_text(json.dumps(upgraded, indent=2))
        tmp.replace(out)
    except OSError as exc:
        print(f"✗ tokenpak pak migrate — cannot write output: {exc}", file=sys.stderr)
        return 1

    print(f"✅ Migrated Pak {pak_id} → {out}")
    print(f"   subtype : {payload.get('pak_type', '?')} → {upgraded['pak_type']}")
    print(f"   checksum: {(declared or '(none)')[:24]}… → {new_checksum[:24]}…")
    print("   pak_id preserved; anchor content unchanged.")
    return 0


def upgrade_pak_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Normalize a Pak payload to the canonical (schema_version 2) file form.

    Pure and idempotent: returns ``(payload_copy, changed)``. Payloads
    already canonical — ``schema_version >= 2`` files, or canonical wire
    payloads from ``Pak.to_dict()`` / the vault adapter — come back
    unchanged with ``changed=False``. Legacy schema_version-1 file-form
    payloads are upgraded:

    - deprecated subtype aliases resolve to their canonical names
    - ``scope`` becomes the canonical ``user``/``project``/``topic``
      record; the legacy ``scope.source_root`` moves to top level
    - ``ttl`` (free-form hint) is renamed ``ttl_hint`` (the canonical
      ``retention.ttl`` bucket is a separate, enumerated field)
    - anchors gain ``anchor_id`` / ``source_hash`` / ``snippet_available``
      (the legacy per-file ``sha256`` key becomes ``source_hash``)
    - canonical ``source`` / ``status`` / ``authority`` / ``confidence`` /
      ``retention`` / ``privacy`` / ``relationships`` records are added

    ``checksum`` and ``pak_id`` are NOT recomputed here — rewriting files
    is ``pak migrate``'s job (it recomputes the checksum and preserves the
    original ``pak_id``). Display callers use this purely in memory.
    """
    import warnings

    from tokenpak.tip.pak import (
        PakAuthority,
        PakConfidence,
        PakPrivacyClass,
        PakStatus,
        PakSubtype,
        default_retention_for,
    )

    sv = payload.get("schema_version")
    if isinstance(sv, int) and sv >= 2:
        return dict(payload), False
    if sv is None and (
        isinstance(payload.get("source"), dict)
        and payload.get("status") is not None
        and payload.get("authority") is not None
    ):
        # Canonical wire form (``Pak.to_dict()``): no schema_version, but
        # the canonical sub-records are present. Nothing to upgrade.
        return dict(payload), False

    up = dict(payload)
    up["schema_version"] = 2

    raw_type = str(up.get("pak_type") or PakSubtype.RECALL.value)
    try:
        with warnings.catch_warnings():
            # The alias resolution below IS the migration — surfacing the
            # DeprecationWarning here would warn users for doing the fix.
            warnings.simplefilter("ignore", DeprecationWarning)
            subtype = PakSubtype.parse(raw_type)
        up["pak_type"] = subtype.value
    except ValueError:
        # Unknown subtype string: keep it verbatim (receivers fall back
        # gracefully on unknown subtypes); retention defaults below use
        # the recall bucket.
        subtype = PakSubtype.RECALL
        up["pak_type"] = raw_type

    old_scope = up.get("scope") if isinstance(up.get("scope"), dict) else {}
    source_root = (old_scope or {}).get("source_root") or up.get("source_root")
    up["scope"] = {"user": None, "project": None, "topic": None}
    if source_root:
        up["source_root"] = str(source_root)

    if "ttl" in up:
        hint = up.pop("ttl")
        if hint and not up.get("ttl_hint"):
            up["ttl_hint"] = hint

    new_anchors: list[dict[str, Any]] = []
    for a in up.get("anchors") or []:
        if not isinstance(a, dict):
            continue
        na = dict(a)
        sha = str(na.pop("sha256", "") or na.get("source_hash", "") or "")
        na["source_hash"] = sha
        na.setdefault("anchor_id", sha[:16] if sha else str(na.get("path", "")))
        na.setdefault("snippet_available", na.get("content") is not None)
        new_anchors.append(na)
    up["anchors"] = new_anchors

    created_at = up.pop("created_at", None) or _utc_now_iso()
    up["source"] = {
        "platform": "tokenpak-cli",
        "source_type": "file",
        "created_at": created_at,
        "source_hash": _aggregate_source_hash(new_anchors),
    }
    up.setdefault("status", PakStatus.PROPOSED.value)
    up.setdefault("authority", PakAuthority.FILE_SOURCE.value)
    up.setdefault("confidence", PakConfidence.HIGH.value)
    up.setdefault("retention", {"ttl": default_retention_for(subtype).value})
    up.setdefault("privacy", {"class": PakPrivacyClass.LOCAL_ONLY.value})
    up.setdefault(
        "relationships",
        {"depends_on": [], "supersedes": [], "related": [], "conflicts_with": []},
    )
    return up, True


def _compute_checksum(payload: dict[str, Any]) -> str:
    """Checksum over the Pak body: sha256 of the sorted-key JSON rendering,
    excluding the ``checksum`` and ``pak_id`` fields themselves.

    This construction is shared by ``create`` (stamping), ``import``
    (verification) and ``migrate`` (re-stamping) and is unchanged from the
    schema_version-1 writer — v1 files verify exactly as before.
    """
    import hashlib

    body = json.dumps(
        {k: v for k, v in payload.items() if k not in ("checksum", "pak_id")},
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _derive_pak_id(checksum: str) -> str:
    """Derive the ``pak:<16-hex>`` id from a ``sha256:...`` checksum."""
    return "pak:" + checksum[len("sha256:") : len("sha256:") + 16]


def _aggregate_source_hash(anchors: list[dict[str, Any]]) -> str:
    """Deterministic sha256 over the anchor set (path + per-file hash pairs).

    Serves as the canonical ``source.source_hash`` for file-form Paks:
    stable across runs for identical directory content, independent of
    embedding choices (content vs reference-only anchors).
    """
    import hashlib

    h = hashlib.sha256()
    for a in sorted(anchors, key=lambda a: str(a.get("path", ""))):
        h.update(str(a.get("path", "")).encode("utf-8"))
        h.update(b"\x00")
        h.update(str(a.get("source_hash", "") or a.get("sha256", "")).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _utc_now_iso() -> str:
    """UTC timestamp in the file-form's ``...Z`` second-resolution format."""
    import datetime

    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
        + "Z"
    )


def _estimate_tokens(anchors: list[dict[str, Any]]) -> int:
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
        from tokenpak.proxy.vault_bridge import get_vault_index

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


def _resolve_vault_pak(pak_ref: str) -> Pak | None:
    """Return a Pak instance for a vault: ID, or None when not indexed."""
    block_id = pak_ref[len("vault:") :]
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


def _print_pak_text(payload: dict[str, Any]) -> None:
    """Render a Pak's metadata — one canonical view for every shape.

    File-form (schema_version 2) and adapter/wire-form payloads share the
    canonical field set, so a single renderer covers both; the file-form
    extension fields print only when present. Legacy schema_version-1
    payloads are normalized in memory first (the on-disk file is left
    untouched) and flagged with a ``pak migrate`` hint.
    """
    display, was_legacy = upgrade_pak_payload(payload)

    print(f"Pak {display.get('pak_id', '?')}")
    print("─" * 40)
    print(f"  type        : {display.get('pak_type', '?')}")
    print(f"  title       : {display.get('title', '')}")
    if display.get("status") is not None:
        print(f"  status      : {display.get('status')}")
    if display.get("authority") is not None:
        print(f"  authority   : {display.get('authority')}")
    if display.get("confidence") is not None:
        print(f"  confidence  : {display.get('confidence')}")
    src = display.get("source") or {}
    if isinstance(src, dict) and src:
        print(f"  source      : {src.get('platform', '?')} ({src.get('source_type', '?')})")
        src_hash = src.get("source_hash", "") or ""
        print(f"  source_hash : {src_hash[:16]}…" if src_hash else "  source_hash : ")
        print(f"  created_at  : {src.get('created_at', '?')}")
    scope = display.get("scope", {}) or {}
    if scope.get("project"):
        print(f"  project     : {scope['project']}")

    # File-form extension fields — present on created/migrated Pak files.
    if display.get("objective"):
        print(f"  objective   : {display['objective']}")
    if display.get("ttl_hint"):
        print(f"  ttl hint    : {display['ttl_hint']}")
    if display.get("token_estimate") is not None:
        print(f"  tokens (est): {display['token_estimate']}")
    if display.get("schema_version") is not None:
        anchors = display.get("anchors") or []
        print(f"  anchors     : {len(anchors)}")
    if display.get("checksum"):
        print(f"  checksum    : {display['checksum'][:32]}…")
    if display.get("source_root"):
        print(f"  source_root : {display['source_root']}")

    if display.get("summary"):
        print()
        print("Summary:")
        print(f"  {display['summary']}")
    if display.get("continuation_notes"):
        print()
        print("Continuation notes:")
        print(f"  {display['continuation_notes']}")
    if was_legacy:
        print()
        print(
            "ℹ️  Legacy Pak schema shown in canonical form — upgrade the file "
            "with: tokenpak pak migrate <pak-file>"
        )


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
    "cmd_pak_create",
    "cmd_pak_export",
    "cmd_pak_import",
    "cmd_pak_inspect",
    "cmd_pak_migrate",
    "cmd_pak_status",
    "upgrade_pak_payload",
]
