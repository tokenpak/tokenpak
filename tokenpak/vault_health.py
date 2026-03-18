"""
vault_health.py — TokenPak Vault Index Health Monitor

Phase 1a: Local detection + on-demand rebuild.

Commands:
    tokenpak vault-health check   -> OK / STALE / MISSING
    tokenpak vault-health repair  -> rebuild index if stale

Exit codes:
    0 — healthy / no action needed
    1 — repaired (index was stale, rebuild succeeded)
    2 — error (index missing or rebuild failed)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default staleness threshold: index older than 24 hours is considered stale
DEFAULT_STALE_SECONDS = 86400  # 24 hours


class IndexStatus:
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    CORRUPT = "CORRUPT"


@dataclass
class HealthCheckResult:
    """Result of a vault-health check."""
    status: str
    index_path: Optional[Path] = None
    block_count: int = 0
    age_seconds: Optional[float] = None
    stale_threshold_seconds: float = DEFAULT_STALE_SECONDS
    error: Optional[str] = None

    def is_ok(self) -> bool:
        return self.status == IndexStatus.OK

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "index_path": str(self.index_path) if self.index_path else None,
            "block_count": self.block_count,
            "age_seconds": round(self.age_seconds, 1) if self.age_seconds is not None else None,
            "stale_threshold_seconds": self.stale_threshold_seconds,
            "error": self.error,
        }


@dataclass
class RepairResult:
    """Result of a vault-health repair operation."""
    success: bool
    files_processed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    index_entries: int = 0
    entries_added: int = 0
    entries_removed: int = 0
    index_size_bytes: int = 0
    rebuild_time_seconds: float = 0.0
    log_entry: str = ""
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "files_processed": self.files_processed,
            "files_skipped": self.files_skipped,
            "files_errored": self.files_errored,
            "index_entries": self.index_entries,
            "entries_added": self.entries_added,
            "entries_removed": self.entries_removed,
            "index_size_bytes": self.index_size_bytes,
            "rebuild_time_seconds": round(self.rebuild_time_seconds, 3),
            "log_entry": self.log_entry,
            "error": self.error,
        }


class VaultHealth:
    """
    Vault index health monitor and rebuilder.

    Parameters
    ----------
    vault_dir : Path | str | None
        Root of the vault. Defaults to ~/vault.
    stale_seconds : float
        Age in seconds after which the index is considered stale.
    """

    def __init__(
        self,
        vault_dir=None,
        stale_seconds: float = DEFAULT_STALE_SECONDS,
    ) -> None:
        if vault_dir is None:
            vault_dir = Path.home() / "vault"
        self.vault_dir = Path(vault_dir).expanduser().resolve()
        self.tokenpak_dir = self.vault_dir / ".tokenpak"
        self.index_path = self.tokenpak_dir / "index.json"
        self.blocks_dir = self.tokenpak_dir / "blocks"
        self.stale_seconds = stale_seconds

    def check(self) -> HealthCheckResult:
        """Check vault index health. Returns OK / STALE / MISSING / CORRUPT."""
        if not self.index_path.exists():
            return HealthCheckResult(
                status=IndexStatus.MISSING,
                index_path=self.index_path,
            )

        try:
            data = json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return HealthCheckResult(
                status=IndexStatus.CORRUPT,
                index_path=self.index_path,
                error=str(exc),
            )

        block_count = len(data.get("blocks", {}))
        age_seconds = self._index_age_seconds()
        stale = age_seconds is not None and age_seconds > self.stale_seconds

        return HealthCheckResult(
            status=IndexStatus.STALE if stale else IndexStatus.OK,
            index_path=self.index_path,
            block_count=block_count,
            age_seconds=age_seconds,
            stale_threshold_seconds=self.stale_seconds,
        )

    def check_index_staleness(self) -> bool:
        """Return True if index is stale, missing, or corrupt."""
        result = self.check()
        return result.status in (IndexStatus.STALE, IndexStatus.MISSING, IndexStatus.CORRUPT)

    def get_status(self) -> str:
        """Return status string: OK / STALE / MISSING / CORRUPT."""
        return self.check().status

    def rebuild_index(self) -> dict:
        """
        Rebuild the vault index by walking vault_dir.

        Returns dict with rebuild metrics (compatible with cli.py cmd_vault_health).
        """
        result = self._do_rebuild()
        if not result.success:
            raise RuntimeError(result.error or "Rebuild failed")
        return result.to_dict()

    def repair(self) -> RepairResult:
        """Detect staleness and rebuild if needed."""
        check_result = self.check()
        if check_result.is_ok():
            return RepairResult(
                success=True,
                index_entries=check_result.block_count,
                log_entry=(
                    f"[{_now_iso()}] OK — index fresh "
                    f"(age {check_result.age_seconds:.0f}s < threshold "
                    f"{self.stale_seconds:.0f}s)"
                ),
            )
        return self._do_rebuild()

    # ------------------------------------------------------------------ #
    #  Internals                                                            #
    # ------------------------------------------------------------------ #

    def _index_age_seconds(self) -> Optional[float]:
        try:
            real_path = self.index_path.resolve()
            mtime = real_path.stat().st_mtime
            return time.time() - mtime
        except OSError:
            return None

    def _do_rebuild(self) -> RepairResult:
        """Walk vault_dir and rebuild index.json + blocks/ .txt files."""
        t0 = time.time()

        TEXT_EXTS = {".md", ".txt", ".rst", ".adoc"}
        CODE_EXTS = {
            ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".rb",
            ".java", ".c", ".cpp", ".h", ".sh", ".bash", ".sql", ".css",
        }
        DATA_EXTS = {".json", ".yaml", ".yml", ".toml", ".csv", ".env", ".cfg", ".ini"}
        SUPPORTED_EXTS = TEXT_EXTS | CODE_EXTS | DATA_EXTS
        SKIP_DIRS = {
            ".git", ".tokenpak", "__pycache__", "node_modules",
            ".venv", "venv", "dist", "build", ".mypy_cache",
        }
        MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB
        MAX_FILES = 50_000

        self.tokenpak_dir.mkdir(parents=True, exist_ok=True)
        self.blocks_dir.mkdir(parents=True, exist_ok=True)

        # Load existing index for incremental merge
        old_blocks: dict = {}
        if self.index_path.exists():
            try:
                old_data = json.loads(self.index_path.read_text(encoding="utf-8"))
                old_blocks = old_data.get("blocks", {})
            except Exception:
                pass

        new_blocks: dict = {}
        files_processed = 0
        files_skipped = 0
        files_errored = 0

        if not self.vault_dir.exists():
            return RepairResult(
                success=False,
                error=f"Vault directory not found: {self.vault_dir}",
            )

        for dirpath, dirnames, filenames in os.walk(str(self.vault_dir)):
            dirnames[:] = [
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".")
            ]

            for filename in filenames:
                if files_processed >= MAX_FILES:
                    break

                filepath = Path(dirpath) / filename
                ext = filepath.suffix.lower()

                if ext not in SUPPORTED_EXTS:
                    files_skipped += 1
                    continue

                try:
                    size = filepath.stat().st_size
                except OSError:
                    files_skipped += 1
                    continue

                if size == 0 or size > MAX_FILE_BYTES:
                    files_skipped += 1
                    continue

                try:
                    rel_path = str(filepath.relative_to(self.vault_dir))
                except ValueError:
                    files_skipped += 1
                    continue

                try:
                    block_id = _make_block_id(rel_path)
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                    content_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
                    tokens_est = len(content) // 4

                    # Incremental: skip unchanged blocks
                    old = old_blocks.get(block_id)
                    if old and old.get("content_hash") == content_hash:
                        new_blocks[block_id] = old
                        files_processed += 1
                        continue

                    # Write block .txt file
                    block_file = self.blocks_dir / f"{block_id}.txt"
                    block_file.write_text(content, encoding="utf-8")

                    frontmatter = _parse_frontmatter(content) if ext == ".md" else {}

                    new_blocks[block_id] = {
                        "block_id": block_id,
                        "source_path": rel_path,
                        "content_hash": content_hash,
                        "raw_tokens": tokens_est,
                        "raw_size": size,
                        "frontmatter": frontmatter,
                        "indexed_at": _now_iso(),
                        "source_type": "filesystem",
                    }
                    files_processed += 1

                except Exception as exc:
                    logger.warning("VaultHealth: error indexing %s: %s", filepath, exc)
                    files_errored += 1

        # Prune blocks for deleted files
        entries_removed = 0
        for bid in list(new_blocks.keys()):
            entry = new_blocks.get(bid)
            if entry:
                src = self.vault_dir / entry.get("source_path", "")
                if not src.exists():
                    del new_blocks[bid]
                    entries_removed += 1
                    block_file = self.blocks_dir / f"{bid}.txt"
                    if block_file.exists():
                        try:
                            block_file.unlink()
                        except OSError:
                            pass

        entries_added = len(new_blocks) - (len(old_blocks) - entries_removed)
        elapsed = time.time() - t0
        index_entries = len(new_blocks)

        index_data = {
            "version": "1.0",
            "meta": {
                "source_dir": str(self.vault_dir),
                "indexed_at": _now_iso(),
                "rebuilt": True,
                "stats": {
                    "total_blocks": index_entries,
                    "files_processed": files_processed,
                    "files_skipped": files_skipped,
                    "files_errored": files_errored,
                    "rebuild_time_seconds": round(elapsed, 3),
                },
            },
            "blocks": new_blocks,
        }

        try:
            self.index_path.write_text(
                json.dumps(index_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            return RepairResult(
                success=False,
                files_processed=files_processed,
                files_skipped=files_skipped,
                files_errored=files_errored,
                error=f"Failed to write index.json: {exc}",
                rebuild_time_seconds=elapsed,
            )

        try:
            index_size = self.index_path.stat().st_size
        except OSError:
            index_size = 0

        log_entry = (
            f"[{_now_iso()}] REBUILT — "
            f"{index_entries} entries, "
            f"{files_processed} files, "
            f"{elapsed:.2f}s"
        )
        _append_log(self.tokenpak_dir, log_entry)

        return RepairResult(
            success=True,
            files_processed=files_processed,
            files_skipped=files_skipped,
            files_errored=files_errored,
            index_entries=index_entries,
            entries_added=max(0, entries_added),
            entries_removed=entries_removed,
            index_size_bytes=index_size,
            rebuild_time_seconds=elapsed,
            log_entry=log_entry,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_block_id(rel_path: str) -> str:
    """Derive a stable block ID from a relative path."""
    return rel_path.replace("/", ".").replace("\\", ".").lstrip(".")


def _parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from a markdown file (fail-silent)."""
    if not content.startswith("---"):
        return {}
    try:
        end = content.index("\n---", 3)
        fm_text = content[3:end].strip()
        result: dict = {}
        for line in fm_text.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                result[key.strip()] = val.strip()
        return result
    except (ValueError, AttributeError):
        return {}


def _append_log(tokenpak_dir: Path, entry: str) -> None:
    """Append a log entry to the health log file (fail-silent)."""
    try:
        log_file = tokenpak_dir / "vault_health.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except OSError:
        pass
