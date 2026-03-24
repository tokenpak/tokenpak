"""Git SourceAdapter for TokenPak.

Reads file content from a local git repository at a given commit SHA.
Uses `git show` via subprocess — no gitpython dependency.
"""

import subprocess
from typing import Tuple

from .base_source import Provenance, SourceAdapter, SourceFetchError

_GIT_TIMEOUT = 15  # seconds


def _run_git(args: list, cwd: str) -> str:
    """Run a git command in cwd; return stdout. Raises SourceFetchError on failure."""
    try:
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if result.returncode != 0:
            raise SourceFetchError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout
    except FileNotFoundError as exc:
        raise SourceFetchError("git not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise SourceFetchError(f"git command timed out: {exc}") from exc


def _resolve_sha(repo_path: str, ref: str = "HEAD") -> str:
    """Resolve a git ref to a full 40-char SHA."""
    sha = _run_git(["rev-parse", ref], cwd=repo_path).strip()
    if len(sha) != 40:
        raise SourceFetchError(f"Unexpected SHA format: {sha!r}")
    return sha


def _read_file_at_commit(repo_path: str, file_path: str, commit_sha: str) -> str:
    """Read file content at a specific commit using `git show`."""
    # git show <sha>:<path>
    return _run_git(["show", f"{commit_sha}:{file_path}"], cwd=repo_path)


class GitAdapter(SourceAdapter):
    """Read file content from a local git repository at a given commit."""

    source_type = "git"

    def ingest(self, source_id: str, **kwargs) -> Tuple[str, Provenance]:
        """
        Fetch a file from a local git repo.

        Args:
            source_id:   File path relative to the repo root (e.g. "src/auth.py").
            repo_path:   Absolute path to the local git repository root (required).
            commit_sha:  Commit SHA or ref to read from (default: HEAD).

        Returns:
            (content, Provenance)
        """
        repo_path = kwargs.get("repo_path")
        commit_sha = kwargs.get("commit_sha", "HEAD")
        if not repo_path:
            raise SourceFetchError("repo_path is required for GitAdapter.ingest()")

        # Resolve ref to full SHA
        full_sha = _resolve_sha(repo_path, commit_sha)

        # Read file content
        content = _read_file_at_commit(repo_path, source_id, full_sha)

        # Title: repo_basename/file_path@short_sha
        import os

        repo_name = os.path.basename(repo_path.rstrip("/"))
        title = f"{repo_name}/{source_id}@{full_sha[:8]}"

        provenance = Provenance(
            source_type=self.source_type,
            source_id=source_id,
            source_version=full_sha,
            fetched_at=self._now(),
            title=title,
        )
        return content, provenance

    def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool:
        """
        Compare current HEAD SHA against cached_version (a full commit SHA).
        Returns True if the file has been modified since cached_version.
        """
        repo_path = kwargs.get("repo_path")
        if not repo_path:
            return False
        try:
            current_sha = _resolve_sha(repo_path, "HEAD")
            if current_sha == cached_version:
                return False
            # Check if the specific file differs between cached and HEAD
            diff_out = _run_git(
                ["diff", "--name-only", cached_version, "HEAD", "--", source_id],
                cwd=repo_path,
            )
            return bool(diff_out.strip())
        except SourceFetchError:
            return False
