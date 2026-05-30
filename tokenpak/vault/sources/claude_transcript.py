"""Claude Code transcript source adapter — Phase 0 / OSS.

Walks ``~/.claude/projects/<project-slug>/<session>.jsonl`` and emits one
block per session into the vault BM25 index.  Blocks are tagged
``source_type='claude_transcript'`` so the proxy search response can label
transcript hits separately from filesystem vault docs.

OFF BY DEFAULT.  Enable with::

    TOKENPAK_INDEX_CLAUDE_TRANSCRIPTS=1

The adapter is **read-only**: transcripts are never modified.  Output blocks
land in the same ``~/vault/.tokenpak/{index.json,blocks/}`` store consumed
by :class:`tokenpak.proxy.vault_bridge.VaultIndex`.  Blocks from other
``source_type`` values (notably ``filesystem``) are left untouched on merge,
and ``vault_health.VaultHealth._do_rebuild`` preserves transcript blocks
across filesystem-vault rebuilds.

Phase 1 / Pro note
------------------
A downstream miner that extracts decisions, incidents, standards-touches,
and handoff-worthy facts into PAKPlan-shaped records belongs in
``tokenpak-paid``, after the scorer ships.
**This module emits no PAKPlan records.**
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

ENV_FLAG = "TOKENPAK_INDEX_CLAUDE_TRANSCRIPTS"
ENV_PROJECTS_DIR = "CLAUDE_PROJECTS_DIR"
SOURCE_TYPE = "claude_transcript"
BLOCK_ID_PREFIX = "claude_transcript"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def default_projects_root() -> Path:
    """Return the Claude Code projects root, honouring ``CLAUDE_PROJECTS_DIR``."""
    override = os.environ.get(ENV_PROJECTS_DIR)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude" / "projects"


def is_enabled() -> bool:
    """Adapter is off-by-default; enable with ``TOKENPAK_INDEX_CLAUDE_TRANSCRIPTS=1``."""
    val = os.environ.get(ENV_FLAG, "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

@dataclass
class TranscriptMessage:
    role: str
    text: str
    timestamp: Optional[str] = None
    cwd: Optional[str] = None


def _extract_text(content: Any) -> str:
    """Coerce a ``message.content`` value (str or list[dict]) into plain text.

    Includes ``text`` blocks and ``tool_result`` text payloads.  Intentionally
    skips ``thinking`` and ``tool_use`` blocks — both add noise to BM25 without
    materially improving recall of decisions or rationale.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif t == "tool_result":
                inner = item.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
                elif isinstance(inner, list):
                    for sub in inner:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            txt = sub.get("text")
                            if isinstance(txt, str):
                                parts.append(txt)
        return "\n".join(p for p in parts if p)
    return ""


def parse_jsonl_session(path: Path) -> list[TranscriptMessage]:
    """Parse a Claude Code transcript file into message records.

    Skips non-message records (``custom-title``, ``file-history-snapshot``,
    ``attachment``), empty content, and the noise marker
    ``<local-command-caveat>``.  Tolerates corrupt JSON lines.
    """
    out: list[TranscriptMessage] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                kind = obj.get("type")
                ts = obj.get("timestamp")
                cwd = obj.get("cwd")

                if kind in ("user", "assistant"):
                    msg = obj.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role") or kind
                    text = _extract_text(msg.get("content"))
                    if not text or "<local-command-caveat>" in text:
                        continue
                    out.append(TranscriptMessage(role=role, text=text, timestamp=ts, cwd=cwd))
                elif kind == "system":
                    content = obj.get("content")
                    text = content if isinstance(content, str) else _extract_text(content)
                    if not text or "<local-command-caveat>" in text:
                        continue
                    out.append(TranscriptMessage(role="system", text=text, timestamp=ts, cwd=cwd))
    except OSError as exc:
        logger.warning("claude_transcript: cannot read %s: %s", path, exc)
        return []
    return out


# ---------------------------------------------------------------------------
# Block emission
# ---------------------------------------------------------------------------

_PROJECT_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize(s: str) -> str:
    return _PROJECT_SLUG_RE.sub("-", s).strip("-") or "session"


def _block_id_for(project_dir: str, session_file: Path) -> str:
    """Stable, filesystem-safe block id."""
    return f"{BLOCK_ID_PREFIX}.{_sanitize(project_dir)}.{_sanitize(session_file.stem)}"


def _decode_project_cwd(project_dir_name: str) -> str:
    """Best-effort decode of Claude Code's project-dir name → original cwd.

    Claude Code encodes the project cwd by replacing ``/`` with ``-`` and
    ``/.`` with ``--``.  Decoding is heuristic — paths containing literal
    hyphens or dots round-trip imperfectly.  Treat the result as a hint, not
    canonical truth.
    """
    if not project_dir_name:
        return ""
    s = project_dir_name
    if s.startswith("-"):
        s = "/" + s[1:]
    s = s.replace("--", "/.")
    s = s.replace("-", "/")
    return s


@dataclass
class TranscriptBlock:
    block_id: str
    session_file: Path
    project_dir: str
    project_cwd_guess: str
    session_id: str
    messages: list[TranscriptMessage]

    def render(self) -> str:
        """Render the session as a deterministic, search-friendly text body."""
        lines: list[str] = [
            f"# Claude Code session {self.session_id}",
            f"project: {self.project_dir}",
        ]
        if self.project_cwd_guess:
            lines.append(f"cwd: {self.project_cwd_guess}")
        lines.append("")
        for m in self.messages:
            header = m.role.upper()
            if m.timestamp:
                header += f" @ {m.timestamp}"
            lines.append(f"## {header}")
            lines.append(m.text)
            lines.append("")
        return "\n".join(lines)


def iter_session_files(root: Optional[Path] = None) -> Iterable[tuple[str, Path]]:
    """Yield ``(project_dir_name, jsonl_path)`` for every session under ``root``."""
    actual = root if root is not None else default_projects_root()
    if not actual.exists():
        return
    for project_dir in sorted(p for p in actual.iterdir() if p.is_dir()):
        for jsonl in sorted(project_dir.glob("*.jsonl")):
            yield project_dir.name, jsonl


def build_block(project_dir_name: str, session_file: Path) -> Optional[TranscriptBlock]:
    msgs = parse_jsonl_session(session_file)
    if not msgs:
        return None
    return TranscriptBlock(
        block_id=_block_id_for(project_dir_name, session_file),
        session_file=session_file,
        project_dir=project_dir_name,
        project_cwd_guess=_decode_project_cwd(project_dir_name),
        session_id=session_file.stem,
        messages=msgs,
    )


# ---------------------------------------------------------------------------
# Index merge
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def index_claude_transcripts(
    tokenpak_dir: Path,
    *,
    projects_root: Optional[Path] = None,
    force: bool = False,
) -> dict:
    """Merge claude_transcript blocks into ``tokenpak_dir/index.json``.

    Existing blocks of any ``source_type`` (filesystem and otherwise) are
    preserved.  Off-by-default — returns ``{"skipped": True}`` unless the
    ``TOKENPAK_INDEX_CLAUDE_TRANSCRIPTS`` env flag is truthy or ``force=True``.

    Returns a stats dict::

        {
            "skipped": False,
            "sessions_seen": N,
            "added": A,
            "updated": U,
            "unchanged": X,
            "total_blocks": T,
        }
    """
    if not force and not is_enabled():
        return {"skipped": True, "reason": "disabled"}

    tokenpak_dir = Path(tokenpak_dir).expanduser()
    index_path = tokenpak_dir / "index.json"
    blocks_dir = tokenpak_dir / "blocks"
    tokenpak_dir.mkdir(parents=True, exist_ok=True)
    blocks_dir.mkdir(parents=True, exist_ok=True)

    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {"version": "1.0", "meta": {}, "blocks": {}}
        except (json.JSONDecodeError, OSError):
            data = {"version": "1.0", "meta": {}, "blocks": {}}
    else:
        data = {"version": "1.0", "meta": {}, "blocks": {}}

    blocks: dict = data.setdefault("blocks", {})

    added = updated = unchanged = 0
    total_seen = 0
    for proj_name, jsonl in iter_session_files(projects_root):
        total_seen += 1
        tb = build_block(proj_name, jsonl)
        if tb is None:
            continue
        rendered = tb.render()
        content_hash = hashlib.sha256(
            rendered.encode("utf-8", errors="replace")
        ).hexdigest()
        bid = tb.block_id

        existing = blocks.get(bid)
        if existing and existing.get("content_hash") == content_hash:
            unchanged += 1
            continue

        block_file = blocks_dir / f"{bid}.txt"
        try:
            block_file.write_text(rendered, encoding="utf-8")
        except OSError as exc:
            logger.warning("claude_transcript: cannot write block %s: %s", bid, exc)
            continue

        try:
            session_size = jsonl.stat().st_size
        except OSError:
            session_size = 0

        first_ts = next((m.timestamp for m in tb.messages if m.timestamp), None)
        last_ts = next(
            (m.timestamp for m in reversed(tb.messages) if m.timestamp), None
        )

        entry = {
            "block_id": bid,
            "source_path": str(jsonl),
            "content_hash": content_hash,
            "raw_tokens": max(1, len(rendered) // 4),
            "raw_size": session_size,
            "frontmatter": {},
            "indexed_at": _now_iso(),
            "source_type": SOURCE_TYPE,
            "claude_transcript": {
                "project_dir": tb.project_dir,
                "project_cwd_guess": tb.project_cwd_guess,
                "session_id": tb.session_id,
                "session_file": str(jsonl),
                "message_count": len(tb.messages),
                "first_timestamp": first_ts,
                "last_timestamp": last_ts,
            },
        }

        if existing:
            updated += 1
        else:
            added += 1
        blocks[bid] = entry

    meta = data.setdefault("meta", {})
    meta.setdefault("source_dir", str(Path.home() / "vault"))
    meta["claude_transcript_last_indexed_at"] = _now_iso()
    meta["claude_transcript_stats"] = {
        "sessions_seen": total_seen,
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
    }

    try:
        index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return {"error": f"write_failed: {exc}"}

    return {
        "skipped": False,
        "sessions_seen": total_seen,
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "total_blocks": len(blocks),
    }
