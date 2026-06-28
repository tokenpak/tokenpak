#!/usr/bin/env python3
"""Full-tree public-leak release gate.

Scans the *shipped* TokenPak artifact tree — the contents of the built sdist
and wheel, or any directory tree — for internal identity / workflow language,
using the SAME forbidden-pattern register and path-scoped allowlists as the
per-PR delta gate (``.github/workflows/identity-language-check.yml``).

Why this exists
---------------
The delta gate only re-checks files **changed in a pull request**
(``git diff`` against base). A reference introduced in a file that no later
PR touches is never re-scanned, so it survives forever and can ship in the
published wheel. This gate closes that gap: it scans the **entire shipped
artifact** on every release preflight (manual ``workflow_dispatch``) and on
every release tag build, *before* the distribution is built and published.

Scope of the pattern register
------------------------------
This gate intentionally mirrors the delta gate's register and allowlists
exactly (agent identities, private home paths, internal task-ID prefixes,
internal standard-number references, and the ``openclaw`` / ``fleet``
path-scoped public-surface allowlists). That is the proven, soaked set and it
directly covers the internal-development-reference class of leak.

It does NOT (yet) implement the broader personal-identifier or credential-shape
categories of the public-safe-defaults policy; extending the register to those
is a deliberate, separately-tracked follow-up.

Scope of the scanned tree
-------------------------
To stay consistent with the delta gate, this scanner applies the same
non-public-surface exclusions: top-level ``tests/`` / ``packages/tests/`` (dev
surfaces) and build-generated manifests (``RECORD`` / ``SOURCES.txt`` / … —
auto-emitted path listings). The in-package ``tokenpak/tests/`` subpackage IS
scanned (it ships in the wheel). The effective scope is therefore the shipped
package source + docs + package metadata.

SYNC OBLIGATION
---------------
The pattern register and the allowlist path-functions below MUST stay
semantically in sync with ``.github/workflows/identity-language-check.yml``.
A follow-up should extract a single shared engine so the two cannot drift.

Usage
-----
    # Scan a built distribution directory (CI default):
    python -m build
    python scripts/release_gate/check_release_leaks.py --dist dist/

    # Scan a directory tree directly (local / fixtures):
    python scripts/release_gate/check_release_leaks.py --tree tokenpak/

Exit status: ``0`` if no unallowlisted match is found, ``1`` otherwise.
"""

from __future__ import annotations

import argparse
import os
import re
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass

# ──────────────────────────────────────────────────────────────────────────
# Forbidden-pattern register (verbatim from identity-language-check.yml).
# Each entry is a POSIX-ERE-compatible regex understood by Python ``re``.
# ──────────────────────────────────────────────────────────────────────────
FLEET = r"\bfleet\b"
OPENCLAW = r"\bopenclaw\b"
CLAUDE_PROJECTS = r"\.claude/projects"

PATTERNS: list[str] = [
    r"\bSue\b",
    r"\bCali\b",
    r"\bTrix\b",
    r"\bSuki\b",
    r"\bAya\b",
    r"\bDee\b",
    r"\bReiPo\b",
    OPENCLAW,
    FLEET,
    r"TSR-[0-9]",
    r"TPS-[0-9]",
    r"CCI-[0-9]",
    r"MTC-[0-9]",
    r"OAS-[0-9]",
    r"TIP7-[0-9]",
    r"TRIX-MTC",
    r"WS-[0-9]",
    r"CCG-[0-9]",
    r"CCP-[0-9]",
    r"VDS-[0-9]",
    r"FIN-[0-9]",
    r"TSG-[0-9]",
    r"TIP-[0-9][0-9]",
    CLAUDE_PROJECTS,
    r"/home/sue/",
    r"~/vault/[0-9][0-9]_",
    r"trixxie168",
    r"\bStd 2[0-9]\b",
    r"\bStd 3[0-9]\b",
    r"Suki: auto-commit",
]

# ──────────────────────────────────────────────────────────────────────────
# Path-scoped allowlists (verbatim from identity-language-check.yml).
# Paths are repository-relative (POSIX separators), e.g. "tokenpak/sdk/openclaw.py".
# ──────────────────────────────────────────────────────────────────────────


def is_release_gate_impl_path(path: str) -> bool:
    """Release-gate implementation surfaces — mask Std 21 / Std 30 references only.

    These human-authored files legitimately implement the release-gate
    standards and reference them by number. All other patterns remain enforced.
    """
    if path in (
        ".github/workflows/release.yml",
        ".github/workflows/release-rehearsal.yml",
        "Makefile",
    ):
        return True
    return path.startswith("scripts/release_gate/")


def is_release_gate_snapshot_path(path: str) -> bool:
    """Generated-artifact snapshot surface — mask Std 21/30 + openclaw + fleet.

    ``tokenpak/_snapshots/*`` capture real generated symbol names
    (``tokenpak.creds.providers.openclaw``, ``tokenpak.cli.fleet`` …) and
    release-gate metadata; those are legitimate. Agent names / task IDs /
    private paths remain enforced even here.
    """
    return path.startswith("tokenpak/_snapshots/")


def is_openclaw_public_path(path: str) -> bool:
    """OpenClaw provider/SDK/integration subsystem — ``openclaw`` is the
    legitimate, non-renamable public provider/module name in these files."""
    if path.startswith("tokenpak/integrations/openclaw/"):
        return True
    if path.startswith("tokenpak/services/routing_service/"):
        return True
    return path in (
        "tokenpak/sdk/openclaw.py",
        "tokenpak/sdk/registry.py",
        "tokenpak/sdk/__init__.py",
        "tokenpak/creds/providers/openclaw.py",
        "tokenpak/creds/providers/__init__.py",
        "tokenpak/creds/router.py",
        "tokenpak/creds/doctor.py",
        "tokenpak/proxy/route_policy.py",
        "tokenpak/tests/test_sdk_openclaw.py",
    )


def is_fleet_public_path(path: str) -> bool:
    """Public ``tokenpak fleet`` feature + functional spend-guard fleet surface —
    every ``fleet`` here is the user-facing multi-instance-proxy capability."""
    if path.startswith("tokenpak/integrations/openclaw/"):
        return True
    return path in (
        "tokenpak/cli/fleet.py",
        "tokenpak/cli/commands/dashboard.py",
        "tokenpak/tests/test_fleet.py",
        "scripts/install-completions.sh",
        "tokenpak/proxy/spend_guard/rolling_caps.py",
    )


def is_perm_tier_cli_path(path: str) -> bool:
    """Public permission-tier / CLI permission surface (v1.9.0 onboarding).

    On these files ``fleet`` is exclusively the launcher **permission tier**
    ``strict|standard|auto|fleet`` (selected via ``--tier fleet`` /
    ``tokenpak permissions set fleet``) and its user-facing "fleet mode"
    unattended-run state — the same legitimate public surface the delta gate
    (identity-language-check.yml) already permits for this feature. Path-scoped
    to ONLY these CLI surfaces, and masks ONLY the ``fleet`` pattern here; every
    other leak class (agent names, internal paths, hostnames, IDs, ``openclaw``)
    is still scanned in these files. Verified: all current ``fleet`` matches in
    these files are the public permission-tier usage; zero are internal-fleet
    ("fleet worker/governor/agents/orchestration") leaks."""
    return path in (
        "tokenpak/cli/commands/permissions.py",
        "tokenpak/cli/commands/integrate.py",
        "tokenpak/cli/commands/menu.py",
        "tokenpak/cli/commands/doctor.py",
        "tokenpak/cli/commands/doctor_claude_code.py",
        "tokenpak/companion/launcher.py",
        "tokenpak/companion/codex/launcher.py",
        "tokenpak/_cli_core.py",
    )


# ──────────────────────────────────────────────────────────────────────────
# Masking (verbatim semantics from identity-language-check.yml
# ``content_for_pattern``). Masks substitute legitimate public-surface forms
# so the forbidden-pattern grep does not match them. Substitutions never add
# or remove newlines, so line numbers are preserved.
# ──────────────────────────────────────────────────────────────────────────


def _mask_snapshot(text: str) -> str:
    text = re.sub(r"\bStd 21\b", "Std __RELGATE_REF_21__", text)
    text = re.sub(r"\bStd 30\b", "Std __RELGATE_REF_30__", text)
    text = re.sub(r"\bopenclaw\b", "__GEN_SYM_OPENCLAW__", text)
    text = re.sub(r"\bfleet\b", "__GEN_SYM_FLEET__", text)
    return text


def _mask_relgate_impl(text: str) -> str:
    text = re.sub(r"\bStd 21\b", "Std __RELGATE_REF_21__", text)
    text = re.sub(r"\bStd 30\b", "Std __RELGATE_REF_30__", text)
    return text


def _mask_openclaw_functional(text: str) -> str:
    text = re.sub(r"tokenpak\.sdk\.openclaw", "tokenpak.sdk.__FUNC_OC_MODULE__", text)
    text = re.sub(
        r"tokenpak\.creds\.providers\.openclaw",
        "tokenpak.creds.providers.__FUNC_OC_MODULE__",
        text,
    )
    text = re.sub(r"x-openclaw-session", "x-__FUNC_OC_HEADER__-session", text)
    text = re.sub(r"x-openclaw-workspace", "x-__FUNC_OC_HEADER__-workspace", text)
    text = re.sub(r'"openclaw"', '"__FUNC_OC_LITERAL__"', text)
    text = re.sub(r"openclaw:main", "__FUNC_OC_CALLER__:main", text)
    text = re.sub(r"openclaw\.json", "__FUNC_OC_CONFIG__.json", text)
    return text


def _mask_fleet_functional(text: str) -> str:
    text = re.sub(r"tokenpak\s+fleet", "tokenpak __PUBLIC_FLEET_VERB__", text)
    text = re.sub(r"--fleet([^a-zA-Z]|$)", r"--__PUBLIC_FLEET_FLAG__\1", text)
    text = re.sub(r"fleet\.yaml", "__PUBLIC_FLEET_CONFIG__.yaml", text)
    text = re.sub(
        r"fleet\s+(config|configuration|command|management|rollup|status)",
        r"__PUBLIC_FLEET_PHRASE__ \1",
        text,
    )
    text = re.sub(r"fleet-telemetry", "__PUBLIC_FLEET_PHRASE__-telemetry", text)
    text = re.sub(r"fleet-wide", "__PUBLIC_FLEET_PHRASE__-wide", text)
    text = re.sub(r"multi-instance\s+fleet", "multi-instance __PUBLIC_TMUX_MODE__", text)
    text = re.sub(r"multi-instance-fleet", "multi-instance-__PUBLIC_TMUX_MODE__", text)
    text = re.sub(r'"fleet"', '"__PUBLIC_FLEET_LITERAL__"', text)
    text = re.sub(r"fleet=", "__PUBLIC_FLEET_KW__=", text)
    text = re.sub(r"if fleet:", "if __PUBLIC_FLEET_PARAM__:", text)
    text = re.sub(r"fleet: bool", "__PUBLIC_FLEET_PARAM__: bool", text)
    text = re.sub(r"a fleet of", "a __PUBLIC_FLEET_PHRASE__ of", text)
    text = re.sub(r"proxy fleet", "proxy __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"fleet manifest", "__PUBLIC_FLEET_PHRASE__ manifest", text)
    text = re.sub(r"fleet automation", "__PUBLIC_FLEET_PHRASE__ automation", text)
    text = re.sub(r"Current fleet", "Current __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"fleet init", "__PUBLIC_FLEET_PHRASE__ init", text)
    text = re.sub(r"agents in fleet", "agents in __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"configured in fleet", "configured in __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"cli\.fleet", "cli.__PUBLIC_FLEET_MODULE__", text)
    text = re.sub(r"configure fleet", "configure __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"=fleet\b", "=__PUBLIC_FLEET_KW__", text)
    text = re.sub(r"x-tokenpak-fleet", "x-tokenpak-__FUNC_FLEET_HDR__", text)
    text = re.sub(r"per-fleet", "per-__FUNC_FLEET_SCOPE__", text)
    text = re.sub(r"fleet-level", "__FUNC_FLEET_SCOPE__-level", text)
    text = re.sub(r"fleet([- ])protection", r"__FUNC_FLEET_SCOPE__\1protection", text)
    text = re.sub(r"fleet caps", "__FUNC_FLEET_SCOPE__ caps", text)
    text = re.sub(r"session ?/ ?fleet ?/ ?agent", "session/__FUNC_FLEET_SCOPE__/agent", text)
    return text


def mask_content(pattern: str, path: str, text: str) -> str:
    """Return ``text`` with the legitimate public-surface forms masked for this
    (pattern, path) pair. Dispatch order mirrors the delta gate exactly."""
    if is_release_gate_snapshot_path(path):
        return _mask_snapshot(text)
    if pattern == FLEET and is_fleet_public_path(path):
        return re.sub(r"\bfleet\b", "__PUBLIC_FLEET_FEATURE__", text)
    if pattern == FLEET and is_perm_tier_cli_path(path):
        return re.sub(r"\bfleet\b", "__PUBLIC_PERMTIER_FLEET__", text)
    if pattern == FLEET:
        return _mask_fleet_functional(text)
    if is_release_gate_impl_path(path):
        return _mask_relgate_impl(text)
    if pattern == OPENCLAW and is_openclaw_public_path(path):
        return re.sub(r"\bopenclaw\b", "__INTEGRATION_OPENCLAW__", text)
    if pattern == OPENCLAW:
        return _mask_openclaw_functional(text)
    if pattern == CLAUDE_PROJECTS:
        return re.sub(r"~/\.claude/projects", "~/__FUNC_CLAUDE_TRANSCRIPT_DIR__", text)
    return text


# ──────────────────────────────────────────────────────────────────────────
# File collection
# ──────────────────────────────────────────────────────────────────────────
# Binary / non-text extensions are skipped (cannot carry readable leaks and
# break utf-8 decoding).
_BINARY_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".pdf",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
    ".pyc",
    ".pyo",
    ".so",
    ".dylib",
    ".dll",
    ".o",
    ".a",
    ".zip",
    ".gz",
    ".tar",
    ".whl",
    ".bz2",
    ".xz",
    ".7z",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".wal",
    ".bin",
    ".mo",
}
_SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}

# Path prefixes that are NOT a public-language-bound surface. These mirror the
# delta gate's changed-files exclusions (identity-language-check.yml) so the two
# gates apply the same scope. ``tests/`` (top-level) and ``packages/tests/`` are
# dev surfaces; the in-package ``tokenpak/tests/`` subpackage is NOT excluded
# here (it ships in the wheel and IS scanned, with its own path-scoped
# allowlist entries). ``sdk/dist/`` is generated SDK output.
_EXCLUDE_PREFIXES = ("tests/", "packages/tests/", "sdk/dist/")

# Build-generated manifest files: auto-emitted path / hash / dependency
# listings, not authored content. They necessarily enumerate legitimate file
# paths (``tokenpak/sdk/openclaw.py`` …) and would false-positive forever.
_MANIFEST_BASENAMES = {
    "RECORD",
    "SOURCES.txt",
    "top_level.txt",
    "entry_points.txt",
    "WHEEL",
    "dependency_links.txt",
    "requires.txt",
    "not-zip-safe",
    "zip-safe",
}


def _is_excluded(relpath: str) -> bool:
    if relpath.startswith(_EXCLUDE_PREFIXES):
        return True
    if ".egg-info/" in relpath:
        return True
    base = relpath.rsplit("/", 1)[-1]
    if base in _MANIFEST_BASENAMES:
        return True
    return False


@dataclass
class ScanFile:
    relpath: str  # repository-relative, POSIX separators
    abspath: str


def _looks_binary(abspath: str) -> bool:
    ext = os.path.splitext(abspath)[1].lower()
    if ext in _BINARY_EXT:
        return True
    try:
        with open(abspath, "rb") as fh:
            chunk = fh.read(4096)
        if b"\x00" in chunk:
            return True
        chunk.decode("utf-8")
    except (UnicodeDecodeError, OSError):
        return True
    return False


def collect_tree(root: str, prefix_strip: str = "") -> list[ScanFile]:
    """Walk ``root`` and return text files as repo-relative ScanFile entries.

    ``prefix_strip`` is removed from the front of each relative path so that
    e.g. an sdist's ``tokenpak-1.2.3/`` top directory maps to repo-relative
    paths the allowlist functions understand.
    """
    out: list[ScanFile] = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            rel = os.path.relpath(abspath, root).replace(os.sep, "/")
            if prefix_strip and rel.startswith(prefix_strip):
                rel = rel[len(prefix_strip) :]
            if _is_excluded(rel):
                continue
            if _looks_binary(abspath):
                continue
            out.append(ScanFile(relpath=rel, abspath=abspath))
    return out


def collect_dist(dist_dir: str, workdir: str) -> list[ScanFile]:
    """Extract every sdist (*.tar.gz) and wheel (*.whl) in ``dist_dir`` into
    ``workdir`` and return their text files as repo-relative ScanFile entries.

    sdist members are ``name-version/<repo path>`` → the top directory is
    stripped. wheel members are already repo-relative for the package
    (``tokenpak/...``) plus a ``*.dist-info/`` metadata dir.
    """
    files: list[ScanFile] = []
    sdists = [f for f in os.listdir(dist_dir) if f.endswith(".tar.gz")]
    wheels = [f for f in os.listdir(dist_dir) if f.endswith(".whl")]
    if not sdists and not wheels:
        raise SystemExit(f"error: no sdist (*.tar.gz) or wheel (*.whl) found in {dist_dir!r}")

    for i, sd in enumerate(sorted(sdists)):
        dest = os.path.join(workdir, f"sdist_{i}")
        os.makedirs(dest, exist_ok=True)
        with tarfile.open(os.path.join(dist_dir, sd)) as tf:
            _safe_extract_tar(tf, dest)
        # sdist root dir is "<name>-<version>/"
        roots = [d for d in os.listdir(dest) if os.path.isdir(os.path.join(dest, d))]
        for r in roots:
            files += collect_tree(os.path.join(dest, r))

    for i, wh in enumerate(sorted(wheels)):
        dest = os.path.join(workdir, f"wheel_{i}")
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(os.path.join(dist_dir, wh)) as zf:
            zf.extractall(dest)
        files += collect_tree(dest)

    return files


def _safe_extract_tar(tf: tarfile.TarFile, dest: str) -> None:
    dest_abs = os.path.abspath(dest)
    for member in tf.getmembers():
        target = os.path.abspath(os.path.join(dest, member.name))
        if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
            raise SystemExit(f"error: unsafe path in archive: {member.name!r}")
    tf.extractall(dest)


# ──────────────────────────────────────────────────────────────────────────
# Scan
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Finding:
    path: str
    line: int
    pattern: str
    text: str


def scan_files(files: list[ScanFile]) -> list[Finding]:
    compiled = [(p, re.compile(p)) for p in PATTERNS]
    findings: list[Finding] = []
    for sf in files:
        try:
            with open(sf.abspath, encoding="utf-8") as fh:
                content = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        orig_lines = content.split("\n")
        for pat, rx in compiled:
            masked = mask_content(pat, sf.relpath, content)
            for idx, mline in enumerate(masked.split("\n")):
                if rx.search(mline):
                    findings.append(
                        Finding(
                            path=sf.relpath,
                            line=idx + 1,
                            pattern=pat,
                            text=orig_lines[idx] if idx < len(orig_lines) else "",
                        )
                    )
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--dist",
        metavar="DIR",
        help="directory containing built sdist (*.tar.gz) and/or wheel (*.whl)",
    )
    src.add_argument(
        "--tree",
        metavar="DIR",
        help="directory tree to scan (repo-relative paths computed from DIR)",
    )
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as workdir:
        if args.dist:
            files = collect_dist(args.dist, workdir)
            source_desc = f"distribution artifacts in {args.dist}"
        else:
            files = collect_tree(args.tree)
            source_desc = f"tree {args.tree}"

        findings = scan_files(files)

    if findings:
        # GitHub-annotation lines + human summary.
        seen = set()
        for f in findings:
            key = (f.path, f.line, f.pattern)
            if key in seen:
                continue
            seen.add(key)
            print(
                f"::error file={f.path},line={f.line}::"
                f"Forbidden pattern '{f.pattern}' in shipped file."
            )
            print(f"  {f.path}:{f.line}: {f.text.strip()}")
        print()
        print(
            f"FAIL: {len(seen)} forbidden-pattern match(es) in {source_desc}.\n"
            "These ship to users. See CONTRIBUTING.md → Public language rules.\n"
            "If a match is a legitimate public surface, add a path-scoped "
            "allowlist entry (and keep it in sync with the delta gate)."
        )
        return 1

    print(f"OK: scanned {len(files)} shipped file(s) in {source_desc}; no leaks found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
