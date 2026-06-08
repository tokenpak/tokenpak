"""ContextProvider interface + Local/Paid implementations (Standards Delta v0 §5.9).

The Dispatch ``ContextProvider`` is the seam between *how context is assembled*
and *what consumes it* (a station run). v0.1-alpha ships exactly one working
provider — :class:`LocalContextProvider` — plus a deliberately-inert
:class:`PaidContextProvider` stub so the Pro-tier boundary is visible
from day one and Pro activation is a constructor swap, not a rewrite.

Contract (Standards Delta v0 §5.9)::

    class ContextProvider(Protocol):
        def build_context(self, manifest, station) -> ContextBundle: ...

Naming note (scope deviation, flagged for review): §5.9 types ``station`` as
``DispatchStation``. No such record exists in the merged schema layer — the
station definition that actually carries declared files / role / overlay is
:class:`tokenpak.orchestration.dispatch.models.route.RouteStation`. This module
therefore types ``build_context``'s ``station`` parameter against
``RouteStation``, which is the real merged contract.

:class:`LocalContextProvider` guarantees (Standards Delta v0 §5.9):

* deterministic given the same inputs (same repo tree + same manifest/station +
  same attachments → byte-identical :class:`ContextBundle`);
* **no LLM call**; **no network call**; **no Pro-tier Pak system dependency**.

It assembles context from five sources, in this fixed precedence order
(earlier sources win on duplicate paths, and the ordering makes the output
deterministic):

1. ``explicit`` — files named on the manifest (via ``Constraint`` / declared
   acceptance hints) and any explicit-file list passed to the provider;
2. ``route_station`` — files declared by the Route/Station config;
3. ``task_frontmatter`` — the current task / frontmatter, if attached;
4. ``manual_attachment`` — manually attached context items;
5. ``repo_scan`` — a simple, gitignore-aware repo scan (lowest precedence;
   only fills remaining budget).

Budgets (Standards Delta v0 §5.9 filters): a per-station **size budget**
(bytes) and a per-station **token budget**. The token budget *inherits the
Spend Guard cap* — in v0.1-alpha it is an injected config value
(:class:`ContextBudget`) with a sane default; the runtime wires the live cap
from Spend Guard through TIP (Standards Delta v0 §8). Both budgets are enforced
greedily in source-precedence order: once adding a file would exceed either
budget the file is **skipped** (not partially included) and recorded in
:attr:`ContextBundle.skipped`, so the bundle always reports exactly what was
left out and why.

gitignore filtering is stdlib-only (``fnmatch`` / ``pathlib``): a lightweight
matcher honoring the common ``.gitignore`` syntax (comments, blanks, negation,
directory-only ``dir/`` patterns, anchored ``/foo`` patterns, ``**`` globs).
This avoids adding ``pathspec`` as a hard runtime dependency — see the packet
report. ``.git/`` is always pruned from the scan regardless of ``.gitignore``.
"""

from __future__ import annotations

import fnmatch
import hashlib
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

from pydantic import Field

from ..models.common import DispatchBaseModel
from ..models.manifest import DispatchManifest
from ..models.route import RouteStation

# ---------------------------------------------------------------------------
# Budget defaults (Standards Delta v0 §5.9)
# ---------------------------------------------------------------------------

# Sane per-station defaults. The token budget is the value that, at runtime,
# the Spend Guard cap overrides (Standards Delta v0 §8). These are deliberately
# conservative placeholders, NOT a Spend Guard policy: this module never reads
# or enforces Spend Guard itself.
DEFAULT_SIZE_BUDGET_BYTES: int = 256 * 1024  # 256 KiB of assembled file content
DEFAULT_TOKEN_BUDGET: int = 64_000  # inherits the live Spend Guard cap at runtime

# Deterministic, network-free token estimate. ~4 chars/token is the standard
# rough heuristic; a fixed divisor keeps the estimate reproducible (no
# tokenizer download, no LLM, no network — Standards Delta v0 §5.9 guarantee).
_CHARS_PER_TOKEN: int = 4


def estimate_tokens(text: str) -> int:
    """Deterministic, offline token estimate for ``text`` (~4 chars/token).

    Ceiling division so any non-empty text costs at least one token. This is a
    reproducible heuristic, not a real tokenizer — Standards Delta v0 §5.9
    forbids network/LLM calls in the local provider.
    """

    if not text:
        return 0
    char_count = len(text)
    return -(-char_count // _CHARS_PER_TOKEN)  # ceil(char_count / _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Source + skip-reason enums
# ---------------------------------------------------------------------------


class ContextSource(str, Enum):
    """Where a context file came from (fixed precedence order, §5.9 inputs).

    Precedence is the declaration order here: ``EXPLICIT`` wins over
    ``REPO_SCAN`` on duplicate paths, and the runner adds sources in this order
    so budget enforcement is deterministic.
    """

    EXPLICIT = "explicit"
    ROUTE_STATION = "route_station"
    TASK_FRONTMATTER = "task_frontmatter"
    MANUAL_ATTACHMENT = "manual_attachment"
    REPO_SCAN = "repo_scan"


class SkipReason(str, Enum):
    """Why a candidate file was left out of the bundle."""

    SIZE_BUDGET_EXCEEDED = "size_budget_exceeded"
    TOKEN_BUDGET_EXCEEDED = "token_budget_exceeded"
    GITIGNORED = "gitignored"
    NOT_FOUND = "not_found"
    NOT_A_FILE = "not_a_file"
    UNREADABLE = "unreadable"
    DUPLICATE = "duplicate"


# ---------------------------------------------------------------------------
# Bundle models (pydantic, mirroring the dispatch model style)
# ---------------------------------------------------------------------------


class ContextFile(DispatchBaseModel):
    """One resolved file included in a :class:`ContextBundle`.

    ``path`` is the repo-relative POSIX path (deterministic across platforms);
    ``content`` is the file's UTF-8 text. ``size_bytes`` / ``token_estimate``
    are this file's contribution to the bundle totals. ``sha256`` is a content
    hash, included so a consumer can detect drift without re-reading the file.
    """

    path: str
    source: ContextSource
    content: str
    size_bytes: int
    token_estimate: int
    sha256: str


class SkippedItem(DispatchBaseModel):
    """A candidate that was NOT included, with the reason it was dropped (§5.9).

    Budget enforcement records every skipped file here so the bundle is a
    complete account of what was considered.
    """

    path: str
    source: ContextSource
    reason: SkipReason
    size_bytes: int | None = Field(
        default=None, description="candidate size when known (None if unreadable)"
    )


class ContextBudget(DispatchBaseModel):
    """Per-station size + token budget for context assembly (§5.9 filters).

    ``token_budget`` inherits the Spend Guard cap at runtime
    (Standards Delta v0 §8); the default here is a placeholder the runtime
    overrides with the live cap. Both budgets are hard ceilings: a file that
    would push a running total over either ceiling is skipped, not truncated.
    """

    size_budget_bytes: int = DEFAULT_SIZE_BUDGET_BYTES
    token_budget: int = DEFAULT_TOKEN_BUDGET


class ContextBundle(DispatchBaseModel):
    """Assembled context returned by :meth:`ContextProvider.build_context` (§5.9).

    Carries the resolved files (with paths + contents), the running totals
    (:attr:`total_size_bytes`, :attr:`token_estimate`), a per-source breakdown
    (:attr:`sources`), and the full skip list (:attr:`skipped`). The bundle is
    deterministic: identical inputs produce an equal bundle.
    """

    manifest_id: str
    station_id: str
    repo_root: str | None = Field(
        default=None, description="repo-scan root (POSIX); None if no scan was run"
    )

    files: list[ContextFile] = Field(default_factory=list)
    skipped: list[SkippedItem] = Field(default_factory=list)

    total_size_bytes: int = 0
    token_estimate: int = 0
    budget: ContextBudget = Field(default_factory=ContextBudget)

    # Per-source count breakdown, e.g. {"explicit": 2, "repo_scan": 5}. Only
    # sources that contributed at least one file appear.
    sources: dict[str, int] = Field(default_factory=dict)

    truncated: bool = Field(
        default=False,
        description="True if any file was skipped for a budget reason",
    )


# ---------------------------------------------------------------------------
# gitignore matcher (stdlib-only; no pathspec hard dependency)
# ---------------------------------------------------------------------------


class _GitignoreRule:
    """One compiled ``.gitignore`` line."""

    __slots__ = ("pattern", "negated", "dir_only", "anchored")

    def __init__(self, raw: str) -> None:
        negated = raw.startswith("!")
        if negated:
            raw = raw[1:]
        dir_only = raw.endswith("/")
        if dir_only:
            raw = raw[:-1]
        anchored = raw.startswith("/")
        if anchored:
            raw = raw[1:]
        self.pattern = raw
        self.negated = negated
        self.dir_only = dir_only
        self.anchored = anchored

    def matches(self, rel_posix: str, is_dir: bool) -> bool:
        """Does this rule match ``rel_posix`` (a repo-relative POSIX path)?"""

        if self.dir_only and not is_dir:
            return False
        pat = self.pattern
        if self.anchored or "/" in pat:
            # Anchored / path-bearing patterns match against the full relative
            # path from the repo root.
            if _fnmatch_path(rel_posix, pat):
                return True
            # A directory pattern also covers everything under it.
            return rel_posix.startswith(pat + "/")
        # Unanchored basename pattern: match the final path component, and also
        # any ancestor directory component (so `build` ignores `a/build/x`).
        parts = rel_posix.split("/")
        for part in parts:
            if fnmatch.fnmatchcase(part, pat):
                return True
        return False


def _fnmatch_path(rel_posix: str, pattern: str) -> bool:
    """fnmatch with ``**`` support for full-path gitignore patterns."""

    if "**" not in pattern:
        return fnmatch.fnmatchcase(rel_posix, pattern)
    # Translate ``**`` (any number of path segments) before delegating the rest
    # of the glob syntax to fnmatch. Split on ``**`` and require each literal
    # chunk to appear in order; ``**`` between them swallows any path span.
    chunks = pattern.split("**")
    pos = 0
    for index, chunk in enumerate(chunks):
        chunk = chunk.strip("/")
        if not chunk:
            continue
        # Try every segment-aligned start position for this chunk.
        matched_here = False
        candidate = rel_posix[pos:]
        segments = candidate.split("/") if candidate else [""]
        prefix = ""
        for seg_i in range(len(segments)):
            sub = "/".join(segments[seg_i:])
            if fnmatch.fnmatchcase(sub, chunk + "*") or fnmatch.fnmatchcase(sub, chunk):
                matched_here = True
                # advance pos past this chunk match (approximate; ordering only)
                advance = candidate.find(chunk, len(prefix))
                if advance >= 0:
                    pos += advance + len(chunk)
                break
            prefix += segments[seg_i] + "/"
        if not matched_here and index != 0:
            return False
        if not matched_here and index == 0 and chunk:
            return False
    return True


class GitignoreFilter:
    """Lightweight gitignore evaluator over a single repo root (stdlib only).

    Loads patterns from the root ``.gitignore`` (the common case for a Dispatch
    repo scan) and evaluates whether a repo-relative path is ignored. Negation
    (``!pat``) is honored using last-match-wins, mirroring git semantics for
    the single-file case. ``.git/`` is always ignored independently of any
    ``.gitignore`` content.

    This is intentionally a subset of full git ignore semantics (no nested
    per-directory ``.gitignore`` chaining, no ``\\`` escaping edge cases) —
    sufficient for the deterministic local context scan and free of any third
    party dependency.
    """

    def __init__(self, rules: list[_GitignoreRule]) -> None:
        self._rules = rules

    @classmethod
    def from_root(cls, root: Path) -> "GitignoreFilter":
        """Build a filter from ``<root>/.gitignore`` (empty filter if absent)."""

        rules: list[_GitignoreRule] = []
        gitignore = root / ".gitignore"
        if gitignore.is_file():
            try:
                text = gitignore.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                rules.append(_GitignoreRule(stripped))
        return cls(rules)

    def is_ignored(self, rel_posix: str, is_dir: bool = False) -> bool:
        """Is ``rel_posix`` (repo-relative POSIX path) ignored?"""

        # `.git` is always pruned, regardless of .gitignore content.
        first = rel_posix.split("/", 1)[0]
        if first == ".git":
            return True
        ignored = False
        for rule in self._rules:
            if rule.matches(rel_posix, is_dir):
                ignored = not rule.negated
        return ignored


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


@runtime_checkable
class ContextProvider(Protocol):
    """Dispatch context-assembly interface (Standards Delta v0 §5.9).

    A provider turns a manifest + station into a :class:`ContextBundle`. The
    OSS implementation is :class:`LocalContextProvider`; the Pro implementation
    is :class:`PaidContextProvider` (stub in v0.1-alpha). Phase D activation is
    a swap of the provider instance, not a rewrite.
    """

    def build_context(
        self,
        manifest: DispatchManifest,
        station: RouteStation,
    ) -> ContextBundle:
        """Assemble and return a :class:`ContextBundle` for ``station``."""
        ...


# ---------------------------------------------------------------------------
# LocalContextProvider (OSS, v0.1-alpha)
# ---------------------------------------------------------------------------


class LocalContextProvider:
    """Deterministic, offline OSS context provider (Standards Delta v0 §5.9).

    Construction:

    * ``repo_root`` — directory the simple repo scan runs over, and the base
      every file path is resolved/relativized against. ``None`` disables the
      repo scan and disables resolving relative declared paths.
    * ``budget`` — per-station :class:`ContextBudget`. The ``token_budget``
      inherits the Spend Guard cap at runtime (Standards Delta v0 §8); pass the
      live cap here. Defaults to the conservative module placeholders.
    * ``explicit_files`` — extra explicit files (highest precedence) beyond
      anything the manifest declares. Paths may be absolute or repo-relative.
    * ``frontmatter_files`` — current-task / frontmatter files to attach.
    * ``manual_attachments`` — manually attached files.
    * ``enable_repo_scan`` — include the gitignore-aware repo scan (default
      ``True`` when ``repo_root`` is set).
    * ``scan_suffixes`` — restrict the repo scan to these suffixes (e.g.
      ``{".py", ".md"}``); ``None`` scans all readable text files.

    Determinism: every list of candidates is processed in a fixed, sorted order
    and budgets are applied greedily, so identical inputs yield an equal
    bundle. No LLM, no network, no Pak dependency.
    """

    def __init__(
        self,
        repo_root: str | Path | None = None,
        *,
        budget: ContextBudget | None = None,
        explicit_files: list[str | Path] | None = None,
        frontmatter_files: list[str | Path] | None = None,
        manual_attachments: list[str | Path] | None = None,
        enable_repo_scan: bool = True,
        scan_suffixes: set[str] | None = None,
    ) -> None:
        self._repo_root = Path(repo_root).resolve() if repo_root is not None else None
        self._budget = budget or ContextBudget()
        self._explicit_files = list(explicit_files or [])
        self._frontmatter_files = list(frontmatter_files or [])
        self._manual_attachments = list(manual_attachments or [])
        self._enable_repo_scan = enable_repo_scan and self._repo_root is not None
        self._scan_suffixes = (
            {s.lower() for s in scan_suffixes} if scan_suffixes is not None else None
        )

    # -- public API -------------------------------------------------------

    def build_context(
        self,
        manifest: DispatchManifest,
        station: RouteStation,
    ) -> ContextBundle:
        """Assemble the :class:`ContextBundle` for ``station`` (§5.9)."""

        gitignore = (
            GitignoreFilter.from_root(self._repo_root)
            if self._repo_root is not None
            else GitignoreFilter([])
        )

        # (path, source) candidate list, in fixed precedence order. Within each
        # explicitly-supplied group, sort by the relative path for determinism.
        candidates: list[tuple[str, ContextSource]] = []
        candidates += self._candidates(self._declared_manifest_files(manifest), ContextSource.EXPLICIT)
        candidates += self._candidates(self._explicit_files, ContextSource.EXPLICIT)
        candidates += self._candidates(self._station_files(station), ContextSource.ROUTE_STATION)
        candidates += self._candidates(self._frontmatter_files, ContextSource.TASK_FRONTMATTER)
        candidates += self._candidates(self._manual_attachments, ContextSource.MANUAL_ATTACHMENT)
        if self._enable_repo_scan:
            candidates += [(p, ContextSource.REPO_SCAN) for p in self._scan_repo(gitignore)]

        files: list[ContextFile] = []
        skipped: list[SkippedItem] = []
        sources: dict[str, int] = {}
        total_bytes = 0
        total_tokens = 0
        truncated = False
        seen: set[str] = set()

        for rel_posix, source in candidates:
            if rel_posix in seen:
                skipped.append(
                    SkippedItem(path=rel_posix, source=source, reason=SkipReason.DUPLICATE)
                )
                continue

            abs_path = self._to_abs(rel_posix)
            if abs_path is None or not abs_path.exists():
                seen.add(rel_posix)
                skipped.append(
                    SkippedItem(path=rel_posix, source=source, reason=SkipReason.NOT_FOUND)
                )
                continue
            if not abs_path.is_file():
                seen.add(rel_posix)
                skipped.append(
                    SkippedItem(path=rel_posix, source=source, reason=SkipReason.NOT_A_FILE)
                )
                continue

            # gitignore filter applies to ALL sources (a declared file that is
            # gitignored is excluded — §5.9 "gitignore-aware path filtering").
            if gitignore.is_ignored(rel_posix, is_dir=False):
                seen.add(rel_posix)
                skipped.append(
                    SkippedItem(path=rel_posix, source=source, reason=SkipReason.GITIGNORED)
                )
                continue

            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                seen.add(rel_posix)
                skipped.append(
                    SkippedItem(path=rel_posix, source=source, reason=SkipReason.UNREADABLE)
                )
                continue

            size_bytes = len(content.encode("utf-8"))
            token_estimate = estimate_tokens(content)

            # Budget enforcement: skip (not truncate) if adding this file would
            # exceed either ceiling. Record the reason. (§5.9 budgets)
            if total_bytes + size_bytes > self._budget.size_budget_bytes:
                seen.add(rel_posix)
                truncated = True
                skipped.append(
                    SkippedItem(
                        path=rel_posix,
                        source=source,
                        reason=SkipReason.SIZE_BUDGET_EXCEEDED,
                        size_bytes=size_bytes,
                    )
                )
                continue
            if total_tokens + token_estimate > self._budget.token_budget:
                seen.add(rel_posix)
                truncated = True
                skipped.append(
                    SkippedItem(
                        path=rel_posix,
                        source=source,
                        reason=SkipReason.TOKEN_BUDGET_EXCEEDED,
                        size_bytes=size_bytes,
                    )
                )
                continue

            seen.add(rel_posix)
            files.append(
                ContextFile(
                    path=rel_posix,
                    source=source,
                    content=content,
                    size_bytes=size_bytes,
                    token_estimate=token_estimate,
                    sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                )
            )
            total_bytes += size_bytes
            total_tokens += token_estimate
            sources[source.value] = sources.get(source.value, 0) + 1

        return ContextBundle(
            manifest_id=manifest.id,
            station_id=station.id,
            repo_root=(self._repo_root.as_posix() if self._repo_root is not None else None),
            files=files,
            skipped=skipped,
            total_size_bytes=total_bytes,
            token_estimate=total_tokens,
            budget=self._budget,
            sources=sources,
            truncated=truncated,
        )

    # -- candidate sources ------------------------------------------------

    @staticmethod
    def _declared_manifest_files(manifest: DispatchManifest) -> list[str]:
        """Explicit files declared by the manifest.

        The manifest's ``path_policy.allowed_paths`` are glob *policies*, not
        concrete files, so they are NOT treated as context candidates. A
        manifest carries explicit context files via deliverables / constraints
        that name a concrete path; v0.1-alpha reads none implicitly — explicit
        files come through the provider's ``explicit_files`` argument. This hook
        exists so a later packet can surface manifest-embedded file lists
        without changing the provider API.
        """

        return []

    @staticmethod
    def _station_files(station: RouteStation) -> list[str]:
        """Files declared by a Route/Station config (§5.9 input 2).

        ``RouteStation`` in the merged schema does not yet carry an explicit
        ``files`` list; this reads any path-like entries a future station gains
        without breaking today. v0.1-alpha returns an empty list when the
        station declares no files.
        """

        declared = getattr(station, "files", None)
        if not declared:
            return []
        return [str(p) for p in declared]

    def _candidates(
        self, paths: list[str | Path], source: ContextSource
    ) -> list[tuple[str, ContextSource]]:
        """Normalize ``paths`` to (rel_posix, source) tuples, sorted for determinism."""

        rels = sorted({self._to_rel(p) for p in paths})
        return [(rel, source) for rel in rels]

    def _scan_repo(self, gitignore: GitignoreFilter) -> list[str]:
        """Simple deterministic repo scan (§5.9 input 3): sorted, gitignore-aware.

        No semantic ranking. Walks ``repo_root`` depth-first in sorted order,
        prunes ignored directories early, and returns repo-relative POSIX paths
        of non-ignored files. Suffix filter (if configured) applied here.
        """

        assert self._repo_root is not None  # guarded by _enable_repo_scan
        results: list[str] = []

        def walk(directory: Path) -> None:
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name)
            except OSError:
                return
            for entry in entries:
                rel = self._to_rel(entry)
                if entry.is_dir():
                    if gitignore.is_ignored(rel, is_dir=True):
                        continue
                    walk(entry)
                elif entry.is_file():
                    if gitignore.is_ignored(rel, is_dir=False):
                        continue
                    if (
                        self._scan_suffixes is not None
                        and entry.suffix.lower() not in self._scan_suffixes
                    ):
                        continue
                    results.append(rel)

        walk(self._repo_root)
        return results

    # -- path helpers -----------------------------------------------------

    def _to_rel(self, path: str | Path) -> str:
        """Repo-relative POSIX string for ``path``.

        Absolute paths under ``repo_root`` are relativized; paths outside the
        root (or when no root is set) keep their given form as a POSIX string.
        """

        p = Path(path)
        if self._repo_root is not None:
            try:
                abs_p = p if p.is_absolute() else (self._repo_root / p)
                return abs_p.resolve().relative_to(self._repo_root).as_posix()
            except ValueError:
                # Outside the repo root — fall through to a plain POSIX rendering.
                pass
        return PurePosixPath(p.as_posix()).as_posix()

    def _to_abs(self, rel_posix: str) -> Path | None:
        """Resolve a repo-relative POSIX path back to an absolute path."""

        p = Path(rel_posix)
        if p.is_absolute():
            return p
        if self._repo_root is not None:
            return self._repo_root / p
        return p


# ---------------------------------------------------------------------------
# PaidContextProvider (stub — interface boundary marker, Pro-tier boundary)
# ---------------------------------------------------------------------------


class PaidContextProvider:
    """Pro context provider — NOT implemented in v0.1-alpha (Standards Delta v0 §5.9).

    Exists from day one so the OSS/Pro boundary is visible and
    Phase D activation is a constructor swap, not a rewrite. The real
    implementation delegates to the ``tokenpak-paid`` Context Package Builder
    over the loopback Pro daemon, falling back to :class:`LocalContextProvider`
    when the daemon is absent. None of that ships in OSS v0.1-alpha.

    Instantiating this class (or calling :meth:`build_context`) raises
    ``NotImplementedError`` so any accidental wiring fails loud rather than
    silently degrading.
    """

    _MESSAGE = (
        "PaidContextProvider is a Pro-tier stub (Standards Delta v0 §5.9): it "
        "delegates to the tokenpak-paid Context Package Builder and is not "
        "available in OSS v0.1-alpha. Use LocalContextProvider."
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError(self._MESSAGE)

    def build_context(
        self,
        manifest: DispatchManifest,
        station: RouteStation,
    ) -> ContextBundle:
        """Always raises ``NotImplementedError`` (Pro path not in v0.1-alpha)."""

        raise NotImplementedError(self._MESSAGE)


__all__ = [
    "ContextProvider",
    "LocalContextProvider",
    "PaidContextProvider",
    "ContextBundle",
    "ContextFile",
    "ContextSource",
    "SkippedItem",
    "SkipReason",
    "ContextBudget",
    "GitignoreFilter",
    "estimate_tokens",
    "DEFAULT_SIZE_BUDGET_BYTES",
    "DEFAULT_TOKEN_BUDGET",
]
