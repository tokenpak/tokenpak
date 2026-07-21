#!/usr/bin/env python3
"""Shared public-safety scanner for public CI surfaces.

This module is the public-side scanner engine. It intentionally uses generic
structural patterns only. Exact private identifiers remain in the internal
register governed by the standards/vault process; public CI reports structural
classes such as ``private-home-path`` or ``internal-task-id-shape``.
"""

from __future__ import annotations

import argparse
import codecs
import os
import re
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatternSpec:
    category: str
    label: str
    regex: str
    flags: int = 0


@dataclass(frozen=True)
class ScanFile:
    relpath: str
    abspath: str


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    category: str
    label: str
    text: str


RELEASE_PATTERN_SPECS: tuple[PatternSpec, ...] = (
    PatternSpec(
        "bot-fleet-alias",
        "internal-fleet-phrase",
        r"\b(?:agent|bot|worker|governor|embedded-agent|runtime|orchestration|cycle|dispatch)\s+fleet\b"
        r"|\bfleet\s+(?:worker|governor|agent|agents|runtime|orchestration|workflow|cycle|dispatch|queue)\b",
        re.IGNORECASE,
    ),
    PatternSpec(
        "private-path",
        "private-home-path",
        r"/(?:home|Users)/(?!<user>(?:/|$)|user(?:/|$)|runner(?:/|$)|workspace(?:/|$)|actions(?:/|$)|tmp(?:/|$))"
        r"[A-Za-z0-9._-]+(?:/|$)",
    ),
    PatternSpec(
        "private-path",
        "vault-path",
        r"(?:~|/(?:home|Users)/[A-Za-z0-9._-]+)/(?:vault)(?:/|$)",
    ),
    PatternSpec(
        "private-path",
        "private-tool-state-path",
        r"(?:~|/(?:home|Users)/[A-Za-z0-9._-]+)/(?:\.openclaw|\.claude/projects)(?:/|$)",
    ),
    PatternSpec(
        "internal-task-id",
        "internal-task-id-shape",
        r"\b(?:tracked|ticket|task|initiative|packet|follow-up|work item|internal id|reference)\s+"
        r"(?:in|as|id|ref|refs|#)?\s*[:#]?\s*"
        r"(?!(?:SHA|SPDX|PEP|RFC|HTTP|HTTPS|TLS|UTF|JSON|YAML|TOML|API|CLI|URL|URI|UUID|OIDC|JWT|DCO|CVE)-)"
        r"[A-Z]{2,8}[0-9]?-(?=[A-Z0-9-]*[0-9])[A-Z0-9][A-Z0-9-]*\b",
        re.IGNORECASE,
    ),
    PatternSpec("internal-standard-ref", "internal-standard-reference", r"\bStd\s+(?:2[0-9]|3[0-9])\b"),
)

BASELINE_EXTRA_PATTERN_SPECS: tuple[PatternSpec, ...] = (
    PatternSpec(
        "personal-identifier",
        "personal-email-domain",
        r"\b[A-Za-z0-9._%+-]+@(?:gmail|yahoo|hotmail|outlook|icloud|protonmail|pm\.me)\.[A-Za-z]{2,}\b",
        re.IGNORECASE,
    ),
    PatternSpec("credential", "aws-access-key-shape", r"\bAKIA[0-9A-Z]{16}\b"),
    PatternSpec("credential", "github-token-shape", r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    PatternSpec(
        "credential",
        "jwt-token-shape",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    ),
    PatternSpec(
        "credential",
        "live-service-token-shape",
        r"\b(?:sk_live_|rk_live_|xox[bpa]-)[A-Za-z0-9_-]{8,}\b",
    ),
    PatternSpec(
        "credential",
        "url-embedded-credentials",
        r"https?://[^:\s/]+:[^@\s/]+@",
    ),
    PatternSpec(
        "credential",
        "credential-assignment-shape",
        r"\b(?:password|passwd|secret|api[_-]?key|token)\s*=\s*[\"'][^\"'<>\s][^\"']{3,}[\"']",
        re.IGNORECASE,
    ),
    PatternSpec(
        "credential",
        "credential-env-assignment",
        r"\b(?:PYPI_TOKEN|ANTHROPIC_API_KEY|OPENAI_API_KEY|GH_TOKEN)\s*=\s*[\"'][^\"']+[\"']",
    ),
)

PATTERN_SPECS: tuple[PatternSpec, ...] = RELEASE_PATTERN_SPECS + BASELINE_EXTRA_PATTERN_SPECS

TEXT_SUFFIXES = frozenset(
    {
        ".cfg",
        ".ini",
        ".js",
        ".json",
        ".jsonl",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }
)

_BINARY_EXT = {
    ".7z",
    ".a",
    ".bin",
    ".bz2",
    ".db",
    ".dll",
    ".dylib",
    ".eot",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mo",
    ".o",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".pyo",
    ".so",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".ttf",
    ".wal",
    ".webp",
    ".whl",
    ".woff",
    ".woff2",
    ".xz",
    ".zip",
}

_SKIP_DIRS = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"}

# Top-level tests and package fixture dirs carry intentional scanner fixtures.
# They are explicitly treated as non-shipping scanner fixtures for this public
# safety scanner; in-package tokenpak/tests remains scanned.
_EXCLUDE_PREFIXES = ("tests/", "packages/tests/", "sdk/dist/")

_MANIFEST_BASENAMES = {
    "RECORD",
    "SOURCES.txt",
    "WHEEL",
    "dependency_links.txt",
    "entry_points.txt",
    "not-zip-safe",
    "requires.txt",
    "top_level.txt",
    "zip-safe",
}


def pattern_specs(profile: str = "release") -> tuple[PatternSpec, ...]:
    if profile == "baseline":
        return PATTERN_SPECS
    if profile == "release":
        return RELEASE_PATTERN_SPECS
    raise ValueError(f"unknown scanner profile: {profile}")


def compiled_patterns(profile: str = "release", specs: tuple[PatternSpec, ...] | None = None):
    if specs is None:
        specs = pattern_specs(profile)
    return [(spec, re.compile(spec.regex, spec.flags)) for spec in specs]


def is_release_gate_impl_path(path: str) -> bool:
    if path in (
        ".github/workflows/release.yml",
        ".github/workflows/release-rehearsal.yml",
        "Makefile",
    ):
        return True
    return path.startswith("scripts/release_gate/")


def is_release_gate_snapshot_path(path: str) -> bool:
    return path.startswith("tokenpak/_snapshots/")


def is_fleet_public_path(path: str) -> bool:
    if path.startswith("tokenpak/integrations/openclaw/"):
        return True
    return path in (
        "scripts/install-completions.sh",
        "tokenpak/cli/commands/dashboard.py",
        "tokenpak/cli/fleet.py",
        "tokenpak/proxy/spend_guard/rolling_caps.py",
        "tokenpak/tests/test_fleet.py",
    )


def is_perm_tier_cli_path(path: str) -> bool:
    return path in (
        "tokenpak/_cli_core.py",
        "tokenpak/cli/commands/doctor.py",
        "tokenpak/cli/commands/doctor_claude_code.py",
        "tokenpak/cli/commands/integrate.py",
        "tokenpak/cli/commands/menu.py",
        "tokenpak/cli/commands/permissions.py",
        "tokenpak/companion/codex/launcher.py",
        "tokenpak/companion/launcher.py",
    )


def _mask_snapshot(text: str) -> str:
    text = re.sub(r"\bStd\s+[0-9]{2}\b", "Std __RELGATE_REF__", text)
    text = re.sub(r"\bopenclaw\b", "__GENERATED_SYMBOL_OPENCLAW__", text)
    text = re.sub(r"\bfleet\b", "__GENERATED_SYMBOL_FLEET__", text)
    return text


def _mask_relgate_impl(text: str) -> str:
    text = re.sub(r"\bStd\s+[0-9]{2}\b", "Std __RELGATE_REF__", text)
    return text


def _mask_fleet_functional(text: str) -> str:
    text = re.sub(r"tokenpak\s+fleet", "tokenpak __PUBLIC_FLEET_VERB__", text)
    text = re.sub(r"--fleet([^a-zA-Z]|$)", r"--__PUBLIC_FLEET_FLAG__\1", text)
    text = re.sub(r"fleet\.yaml", "__PUBLIC_FLEET_CONFIG__.yaml", text)
    text = re.sub(
        r"fleet\s+(config|configuration|command|management|rollup|status|stats)",
        r"__PUBLIC_FLEET_PHRASE__ \1",
        text,
    )
    text = re.sub(r"fleet\s+agent\s+data", "__PUBLIC_FLEET_PHRASE__ agent data", text)
    text = re.sub(r"fleet-telemetry", "__PUBLIC_FLEET_PHRASE__-telemetry", text)
    text = re.sub(r"fleet-wide", "__PUBLIC_FLEET_PHRASE__-wide", text)
    text = re.sub(r"in\s+the\s+fleet", "in the __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"for\s+the\s+fleet", "for the __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"to\s+the\s+fleet", "to the __PUBLIC_FLEET_PHRASE__", text)
    text = re.sub(r"multi-instance\s+fleet", "multi-instance __PUBLIC_TMUX_MODE__", text)
    text = re.sub(r"multi-instance-fleet", "multi-instance-__PUBLIC_TMUX_MODE__", text)
    text = re.sub(r'"fleet"', '"__PUBLIC_FLEET_LITERAL__"', text)
    text = re.sub(r"`fleet`", "`__PUBLIC_FLEET_LITERAL__`", text)
    text = re.sub(r"'fleet'", "'__PUBLIC_FLEET_LITERAL__'", text)
    text = re.sub(r"fleet=", "__PUBLIC_FLEET_KW__=", text)
    text = re.sub(r"if fleet:", "if __PUBLIC_FLEET_PARAM__:", text)
    text = re.sub(r"if not fleet:", "if not __PUBLIC_FLEET_PARAM__:", text)
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
    text = re.sub(r"--tier\s+fleet", "--tier __PUBLIC_PERMTIER_FLEET__", text)
    text = re.sub(r"--tier=fleet", "--tier=__PUBLIC_PERMTIER_FLEET__", text)
    text = re.sub(r"permissions\s+set\s+fleet", "permissions set __PUBLIC_PERMTIER_FLEET__", text)
    text = re.sub(r"fleet\s+mode", "__PUBLIC_PERMTIER_FLEET__ mode", text)
    text = re.sub(r"fleet-mode", "__PUBLIC_PERMTIER_FLEET__-mode", text)
    text = re.sub(r"auto/fleet", "auto/__PUBLIC_PERMTIER_FLEET__", text)
    return text


def mask_content(spec: PatternSpec, path: str, text: str) -> str:
    if is_release_gate_snapshot_path(path):
        return _mask_snapshot(text)
    if spec.label == "internal-standard-reference" and is_release_gate_impl_path(path):
        return _mask_relgate_impl(text)
    if spec.label == "internal-fleet-phrase":
        if is_fleet_public_path(path) or is_perm_tier_cli_path(path):
            return _mask_fleet_functional(text)
        return _mask_fleet_functional(text)
    if spec.label == "private-tool-state-path":
        text = re.sub(r"~/\.claude/projects", "~/__PUBLIC_TRANSCRIPT_DIR__", text)
        return text
    return text


def is_excluded(relpath: str) -> bool:
    relpath = relpath.replace(os.sep, "/")
    if relpath.startswith(_EXCLUDE_PREFIXES):
        return True
    if ".egg-info/" in relpath:
        return True
    base = relpath.rsplit("/", 1)[-1]
    return base in _MANIFEST_BASENAMES


def _git_root(root: str) -> str | None:
    try:
        raw = subprocess.check_output(
            ["git", "-C", root, "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return os.path.abspath(raw.strip())


def looks_binary(abspath: str) -> bool:
    ext = os.path.splitext(abspath)[1].lower()
    if ext in _BINARY_EXT:
        return True
    try:
        with open(abspath, "rb") as fh:
            chunk = fh.read(4096)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        decoder.decode(chunk, final=False)
    except UnicodeDecodeError:
        return True
    return False


def collect_tree(root: str, prefix_strip: str = "") -> list[ScanFile]:
    out: list[ScanFile] = []
    root = os.path.abspath(root)
    repo_root = _git_root(root)
    rel_base = repo_root if repo_root and root.startswith(repo_root + os.sep) else root
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            abspath = os.path.join(dirpath, name)
            rel = os.path.relpath(abspath, rel_base).replace(os.sep, "/")
            if prefix_strip and rel.startswith(prefix_strip):
                rel = rel[len(prefix_strip) :]
            if is_excluded(rel) or looks_binary(abspath):
                continue
            out.append(ScanFile(relpath=rel, abspath=abspath))
    return out


def collect_git_tracked(root: str) -> list[ScanFile]:
    root_path = Path(root).resolve()
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(root_path), "ls-files", "-z"],
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return collect_tree(str(root_path))
    out: list[ScanFile] = []
    for rel in raw.decode("utf-8", errors="ignore").split("\0"):
        if not rel:
            continue
        abspath = root_path / rel
        if not abspath.is_file() or is_excluded(rel) or looks_binary(str(abspath)):
            continue
        out.append(ScanFile(relpath=rel.replace(os.sep, "/"), abspath=str(abspath)))
    return out


def collect_paths(root: str, paths: list[str]) -> list[ScanFile]:
    root_path = Path(root).resolve()
    out: list[ScanFile] = []
    for raw in paths:
        rel = raw.strip().replace(os.sep, "/")
        if not rel or is_excluded(rel):
            continue
        if Path(rel).suffix and Path(rel).suffix not in TEXT_SUFFIXES:
            continue
        abspath = root_path / rel
        if not abspath.is_file() or looks_binary(str(abspath)):
            continue
        out.append(ScanFile(relpath=rel, abspath=str(abspath)))
    return out


def collect_dist(dist_dir: str, workdir: str) -> list[ScanFile]:
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


def scan_text(path: str, text: str, patterns=None, profile: str = "release") -> list[Finding]:
    compiled = patterns if patterns is not None else compiled_patterns(profile)
    findings: list[Finding] = []
    orig_lines = text.split("\n")
    for spec, rx in compiled:
        masked = mask_content(spec, path, text)
        for idx, line in enumerate(masked.split("\n")):
            if rx.search(line):
                findings.append(
                    Finding(
                        path=path,
                        line=idx + 1,
                        category=spec.category,
                        label=spec.label,
                        text=orig_lines[idx] if idx < len(orig_lines) else "",
                    )
                )
    return findings


def scan_files(files: list[ScanFile], patterns=None, profile: str = "release") -> list[Finding]:
    compiled = patterns if patterns is not None else compiled_patterns(profile)
    findings: list[Finding] = []
    for sf in files:
        try:
            with open(sf.abspath, encoding="utf-8") as fh:
                content = fh.read()
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(sf.relpath, content, compiled))
    return findings


def read_paths_file(path: str) -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def report_findings(findings: list[Finding], source_desc: str, annotation: str) -> None:
    seen = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.line, finding.category, finding.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    for finding in unique:
        if annotation != "none":
            print(
                f"::{annotation} file={finding.path},line={finding.line}::"
                f"public-safety[{finding.category}] matched {finding.label}."
            )
        print(f"  {finding.path}:{finding.line}: {finding.label}")
    print()
    print(f"FAIL: {len(unique)} public-safety finding(s) in {source_desc}.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--dist", metavar="DIR")
    src.add_argument("--tree", metavar="DIR")
    src.add_argument("--git-tracked", metavar="DIR")
    src.add_argument("--paths-from", metavar="FILE")
    src.add_argument("--paths", nargs="*")
    ap.add_argument("--root", default=".")
    ap.add_argument("--annotation", choices=["error", "warning", "none"], default="error")
    ap.add_argument("--profile", choices=["release", "baseline"], default="release")
    ap.add_argument("--recommended", action="store_true", help="report findings without a non-zero exit")
    args = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as workdir:
        if args.dist:
            files = collect_dist(args.dist, workdir)
            source_desc = f"distribution artifacts in {args.dist}"
        elif args.tree:
            files = collect_tree(args.tree)
            source_desc = f"tree {args.tree}"
        elif args.git_tracked:
            files = collect_git_tracked(args.git_tracked)
            source_desc = f"tracked tree {args.git_tracked}"
        elif args.paths_from:
            files = collect_paths(args.root, read_paths_file(args.paths_from))
            source_desc = f"changed files from {args.paths_from}"
        else:
            files = collect_paths(args.root, args.paths)
            source_desc = "changed file arguments"

        findings = scan_files(files, profile=args.profile)

    if findings:
        report_findings(findings, source_desc, args.annotation)
        return 0 if args.recommended else 1

    print(f"OK: scanned {len(files)} file(s) in {source_desc}; no public-safety findings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
