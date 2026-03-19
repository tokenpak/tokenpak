# SPDX-License-Identifier: MIT
"""ContextAssembler — TokenPak Protocol Phase 1: CANON references + STATE_JSON.

Builds the wire-format context payload for LLM requests. Deduplicates
stable blocks by sending them in full only on the first turn (or when
their content version changes); subsequent turns send a compact reference.

Reference format: @BLOCK_ID#vN

Wire format example:
  CANON:
    SOUL=[full content OR @SOUL#v12]
    TOOLS=@TOOLS#v27
    SKILLS=[@SKILL.heartbeat#v4,@SKILL.pdf_parse#v3]

  STATE_JSON:
  {"goal":"...","current_task":"...","done":[...],"open":[...],"next":[...]}
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, Optional, Tuple


class CanonBlockRegistry:
    """
    Lightweight file-based registry for CANON blocks.

    Stores canonical block wire text at:
      .tokenpak/blocks/BLOCK_ID@vN.tpkb

    Tracks versions in manifest:
      .tokenpak/blocks/manifest.json  →  {block_id: {hash, version}}
    """

    def __init__(self, base_dir: str = ".tokenpak"):
        self.blocks_dir = Path(base_dir) / "blocks"
        self.blocks_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.blocks_dir / "manifest.json"
        self._manifest: Dict[str, dict] = self._load_manifest()

    # ── Manifest I/O ─────────────────────────────────────────────────────────

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                with open(self.manifest_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_manifest(self) -> None:
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, indent=2)

    # ── Core API ─────────────────────────────────────────────────────────────

    def _content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def get_or_register(self, block_id: str, content: str) -> Tuple[str, bool]:
        """
        Register or look up a CANON block.

        Returns:
            (version_str, is_new_or_changed)
              - version_str: e.g. "v1", "v2"
              - is_new_or_changed: True when caller should inline content
        """
        new_hash = self._content_hash(content)
        entry = self._manifest.get(block_id)

        if entry is None:
            # First time seeing this block_id
            version = 1
        elif entry["hash"] != new_hash:
            # Content changed → bump version
            version = entry["version"] + 1
        else:
            # Same content — return existing version
            return f"v{entry['version']}", False

        version_str = f"v{version}"

        # Persist block content to .tokenpak/blocks/BLOCK_ID@vN.tpkb
        block_path = self.blocks_dir / f"{block_id}@{version_str}.tpkb"
        block_path.write_text(content, encoding="utf-8")

        # Update manifest
        self._manifest[block_id] = {
            "hash": new_hash,
            "version": version,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._save_manifest()

        return version_str, True

    def current_version(self, block_id: str) -> Optional[str]:
        """Return current version string for a block_id, or None if unknown."""
        entry = self._manifest.get(block_id)
        if entry:
            return f"v{entry['version']}"
        return None

    def read_block_content(self, block_id: str, version_str: str) -> Optional[str]:
        """Read stored .tpkb content for a block/version pair."""
        block_path = self.blocks_dir / f"{block_id}@{version_str}.tpkb"
        if block_path.exists():
            return block_path.read_text(encoding="utf-8")
        return None


# ─────────────────────────────────────────────────────────────────────────────


class ContextAssembler:
    """
    Assembles TokenPak wire-format context payloads.

    Session state (which blocks have been sent at which version) is
    persisted to .tokenpak/state/session_<id>.state.json so it survives
    across turns without holding all context in memory.

    Usage:
        assembler = ContextAssembler(session_id="abc123")

        # First turn — inlines SOUL.md, sends ref for TOOLS if unchanged
        canon = assembler.assemble_context({
            "SOUL":  (soul_content, None),   # version auto-detected
            "TOOLS": (tools_content, None),
        })
        # canon → "CANON:\n  SOUL=[full content]\n  TOOLS=[full content]"

        # Second turn — sends refs only
        canon = assembler.assemble_context({...})
        # canon → "CANON:\n  SOUL=@SOUL#v1\n  TOOLS=@TOOLS#v1"
    """

    def __init__(self, session_id: str, base_dir: str = ".tokenpak"):
        self.session_id = session_id
        self.base_dir = Path(base_dir)
        self.canon_registry = CanonBlockRegistry(base_dir=base_dir)

        # Persistent session state
        session_dir = self.base_dir / "state"
        session_dir.mkdir(parents=True, exist_ok=True)
        self._session_path = session_dir / f"session_{session_id}.state.json"
        self._session = self._load_session()

    # ── Session I/O ──────────────────────────────────────────────────────────

    def _load_session(self) -> dict:
        if self._session_path.exists():
            try:
                with open(self._session_path, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "session_id": self.session_id,
            "turn": 0,
            "sent_blocks": {},
            "last_updated": "",
        }

    def _save_session(self) -> None:
        self._session["turn"] = self._session.get("turn", 0) + 1
        self._session["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self._session_path, "w", encoding="utf-8") as f:
            json.dump(self._session, f, indent=2)

    @property
    def sent_blocks(self) -> Dict[str, str]:
        """Map of {block_id: version_str} for blocks already sent this session."""
        return self._session.get("sent_blocks", {})

    # ── Core API ─────────────────────────────────────────────────────────────

    def add_canon_block(
        self, block_id: str, block_content: str, version: Optional[str] = None
    ) -> str:
        """
        Produce the wire entry for one CANON block.

        - First time block is sent in this session: inline full content.
        - Subsequent turns with same version: reference only (@BLOCK_ID#vN).
        - Version changed: inline new content, update session record.

        Args:
            block_id:      e.g. "SOUL", "TOOLS", "SKILL.heartbeat"
            block_content: full text of the block
            version:       if None, auto-detected via CanonBlockRegistry

        Returns:
            e.g.  'SOUL=[full SOUL.md content]'
               or 'SOUL=@SOUL#v1'
        """
        # Register/look up block version
        detected_version, content_changed = self.canon_registry.get_or_register(
            block_id, block_content
        )
        effective_version = version or detected_version

        # Decide whether to inline or reference
        already_sent_version = self.sent_blocks.get(block_id)
        should_inline = (
            already_sent_version is None  # never sent
            or already_sent_version != effective_version  # version changed
        )

        if should_inline:
            self._session.setdefault("sent_blocks", {})[block_id] = effective_version
            return f"{block_id}={block_content}"
        else:
            return f"{block_id}=@{block_id}#{effective_version}"

    def assemble_context(
        self,
        required_blocks: Dict[str, Tuple[str, Optional[str]]],
        save_session: bool = True,
    ) -> str:
        """
        Build the full CANON section for a request payload.

        Args:
            required_blocks: {block_id: (content, version_or_None)}
            save_session:    persist updated session state after assembly

        Returns:
            Multi-line CANON: section string.
        """
        canon_lines = []
        for block_id, (content, version) in required_blocks.items():
            entry = self.add_canon_block(block_id, content, version)
            canon_lines.append(entry)

        if save_session:
            self._save_session()

        if not canon_lines:
            return "CANON:"

        return "CANON:\n  " + "\n  ".join(canon_lines)

    def assemble_full_payload(
        self,
        required_blocks: Dict[str, Tuple[str, Optional[str]]],
        state_manager=None,
        evidence_pack=None,
        recent_text: str = "",
        tools_text: str = "",
        budgeter=None,
    ) -> str:
        """
        Build the complete TokenPak payload: CANON section + optional STATE_JSON.

        Optionally enforces token budget via a Budgeter instance before
        assembling the final payload.

        Args:
            required_blocks: passed to assemble_context()
            state_manager:   optional StateManager; if provided, appends STATE_JSON
            evidence_pack:   optional EvidencePack; if provided, appends EVIDENCE section
            recent_text:     optional recent conversation context
            tools_text:      optional tool/skill schema text
            budgeter:        optional Budgeter; enforces total_tokens budget

        Returns:
            Full payload string ready to prepend to a request.
        """
        # Apply budget constraints before assembling if budgeter provided
        if budgeter is not None and (evidence_pack or recent_text or tools_text):
            components = {
                "state": {
                    "text": state_manager.to_wire_format() if state_manager else "",
                    "priority": "critical",
                },
                "recent": {"text": recent_text, "priority": "high"},
                "evidence": {
                    "items": evidence_pack.items if evidence_pack else [],
                    "priority": "medium",
                },
                "tools": {"text": tools_text, "priority": "variable"},
            }
            trimmed = budgeter.allocate(components)

            # Rebuild evidence_pack from trimmed items (if trimmed)
            if evidence_pack and trimmed.get("evidence"):
                from .evidence_pack import EvidencePack

                new_pack = EvidencePack()
                new_pack.items = trimmed["evidence"]["items"]
                evidence_pack = new_pack

            recent_text = trimmed.get("recent", {}).get("text", recent_text)
            tools_text = trimmed.get("tools", {}).get("text", tools_text)

        # Build sections
        canon_section = self.assemble_context(required_blocks)
        parts = [canon_section]

        if state_manager is not None:
            parts.append(state_manager.to_wire_section())

        if evidence_pack is not None and len(evidence_pack) > 0:
            parts.append(evidence_pack.to_wire_format())

        if recent_text:
            parts.append(f"RECENT:\n{recent_text}")

        if tools_text:
            parts.append(f"TOOLS:\n{tools_text}")

        return "\n\n".join(parts)

    # ── Diagnostics ──────────────────────────────────────────────────────────

    def session_summary(self) -> dict:
        """Return current session metadata for logging/debugging."""
        return {
            "session_id": self.session_id,
            "turn": self._session.get("turn", 0),
            "blocks_sent": len(self.sent_blocks),
            "sent_blocks": dict(self.sent_blocks),
            "last_updated": self._session.get("last_updated", ""),
        }

    def __repr__(self) -> str:
        return (
            f"<ContextAssembler session={self.session_id!r} "
            f"turn={self._session.get('turn', 0)} "
            f"blocks_sent={len(self.sent_blocks)}>"
        )
