# SPDX-License-Identifier: Apache-2.0
"""Atomic file writer for scaffold artifacts. Honors dry-run.

The writer is the only place that touches disk. Receives the
guardrail-checked artifact list, writes new files, refuses to
overwrite. In dry-run mode, prints what would be written to stdout
and returns without touching disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from ._generator import GeneratedArtifact

# Repo root used for canonical-layout artifacts. Resolved relative
# to this module's location: tokenpak/scaffold/_writer.py is two
# directories deep from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class WriteResult:
    """What :func:`write_artifacts` did. Returned to the CLI for
    formatting + exit-code decisions.
    """

    written_paths: List[Path] = field(default_factory=list)
    skipped_existing: List[Path] = field(default_factory=list)
    instructions: List[str] = field(default_factory=list)
    """Multi-line text the CLI should print at the end of the run
    (paste-ready issue body, register() call to add manually, etc.).
    """
    dry_run: bool = False


def write_artifacts(
    artifacts: List[GeneratedArtifact],
    *,
    dry_run: bool = False,
) -> WriteResult:
    """Write the artifact list. In dry-run, return what would be
    written without touching disk.

    Refuses to overwrite existing files (each conflicting path is
    recorded under :attr:`WriteResult.skipped_existing` and surfaced
    in the CLI summary).

    For ``kind == "instructions"`` artifacts, the content is
    accumulated in :attr:`WriteResult.instructions` for the CLI to
    print rather than written to disk.
    """
    result = WriteResult(dry_run=dry_run)

    for art in artifacts:
        if art.kind == "instructions":
            result.instructions.append(art.content)
            continue

        target = _resolve_path(art.relative_path)

        if target.exists():
            result.skipped_existing.append(target)
            continue

        if dry_run:
            result.written_paths.append(target)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: temp file then rename.
        tmp = target.with_suffix(target.suffix + ".scaffold.tmp")
        tmp.write_text(art.content, encoding="utf-8")
        tmp.rename(target)
        result.written_paths.append(target)

    return result


def _resolve_path(rel: str) -> Path:
    """Map an artifact's ``relative_path`` to an absolute Path.

    Absolute paths (when ``--out-dir`` was set) are honored as-is.
    Otherwise resolve under the repo root.
    """
    p = Path(rel)
    if p.is_absolute():
        return p
    return _REPO_ROOT / p


def format_summary(
    result: WriteResult,
    params_slug: str,
    *,
    class_basename: str = "",
    vendor_safe: str = "",
) -> str:
    """Human-friendly stdout summary.

    Prints written / skipped / register-patch / next-steps /
    follow-up-issue sections. The register-patch block (§2.5 of the
    Phase 4 spec, hardened in 4.1) shows the EXACT lines the
    maintainer adds to ``credential_injector.py`` so paste-merging
    is mechanical, not reverse-engineered from the file structure.
    """
    lines: List[str] = []

    header = "[scaffold] Dry run — no files written" if result.dry_run else "[scaffold] Wrote:"
    lines.append(header)
    for p in result.written_paths:
        lines.append(f"  {p}")

    if result.skipped_existing:
        lines.append("")
        lines.append(
            "[scaffold] Skipped (file already exists; "
            "not overwriting):"
        )
        for p in result.skipped_existing:
            lines.append(f"  {p}")

    lines.append("")
    lines.append(f"[scaffold] Provider scaffolded: {params_slug}")

    # Phase 4.1 hardening — emit the exact register() patch the
    # maintainer needs to apply. Two anchors so the maintainer can
    # find both insertion points in credential_injector.py:
    if class_basename and vendor_safe:
        cls_name = f"{class_basename}CredentialProvider"
        lines.append("")
        lines.append("[scaffold] Apply this register() patch to wire the provider in:")
        lines.append("")
        lines.append(
            "  Edit tokenpak/services/routing_service/credential_injector.py\n"
            "\n"
            "  ① Add the import (near other extras imports, BEFORE the\n"
            "     ``# ── Register built-ins at import`` comment block):\n"
            "\n"
            f"     from tokenpak.services.routing_service.extras.{vendor_safe} import (\n"
            f"         {cls_name},\n"
            "     )\n"
            "\n"
            "  ② Append a register() call to the existing register block\n"
            "     (after the last existing register(...) line):\n"
            "\n"
            f"     register({cls_name}())\n"
            "\n"
            "  ③ (Optional) Add the class to ``__all__`` in alphabetical order:\n"
            "\n"
            f'     "{cls_name}",\n'
            "\n"
            "  Future versions of the scaffolder may apply this patch\n"
            "  automatically via ``--register`` (see Phase 4.1)."
        )

    lines.append("")
    test_filename = f"test_{vendor_safe}_offline.py" if vendor_safe else "test_<vendor>_offline.py"
    lines.append(
        "[scaffold] Next steps:\n"
        f"  1. Apply the register() patch above (or re-run with --register).\n"
        f"  2. Run: pytest tests/{test_filename}\n"
        "  3. Run: ruff check tokenpak/ tests/\n"
        "  4. Open a PR per Standard #21 (branching policy).\n"
        "  5. After live verification: flip live_verified=True + update docstring."
    )

    if result.instructions:
        lines.append("")
        lines.append("[scaffold] Suggested follow-up issue (paste into `gh issue create`):")
        lines.append("")
        for instr in result.instructions:
            for line in instr.splitlines():
                lines.append(f"  {line}")

    return "\n".join(lines)
