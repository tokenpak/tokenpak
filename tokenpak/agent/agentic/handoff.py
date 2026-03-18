"""tokenpak.agent.agentic.handoff — Context handoff between agents.

Allows agents to pass context references, summaries, and next steps
to each other. Handoffs are stored as JSON in ~/.tokenpak/handoffs/.

Public API:
    manager = HandoffManager()
    handoff = manager.create_handoff(
        from_agent="cali",
        to_agent="sue",
        context_refs=[ContextRef(type="file", path="/path/to/file")],
        what_was_done="Implemented X",
        whats_next="Review Y",
    )
    handoff = manager.receive_handoff(handoff.id)   # validate refs
    handoff = manager.apply_handoff(handoff.id)     # load context, mark applied
    manager.expire_stale()                          # auto-expire past TTL
    handoffs = manager.list_handoffs(to_agent="sue")
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_HANDOFF_DIR = Path.home() / ".tokenpak" / "handoffs"
DEFAULT_TTL_HOURS = 24
REGISTERED_AGENTS = {"cali", "sue", "trix", "kevin"}


class HandoffStatus(str, Enum):
    PENDING = "pending"  # created, not yet received
    RECEIVED = "received"  # validated and acknowledged
    APPLIED = "applied"  # context loaded into working set
    EXPIRED = "expired"  # TTL passed without being applied
    INVALID = "invalid"  # refs could not be validated


@dataclass
class ContextRef:
    """A single context reference passed in a handoff."""

    type: str  # "file", "note", "url", "snippet", "task"
    path: str  # path, URL, or identifier
    description: str = ""
    valid: Optional[bool] = None  # set during receive_handoff validation

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ContextRef":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Handoff:
    """A context handoff record."""

    id: str
    from_agent: str
    to_agent: str
    context_refs: List[ContextRef] = field(default_factory=list)
    status: HandoffStatus = HandoffStatus.PENDING
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + DEFAULT_TTL_HOURS * 3600)
    received_at: Optional[float] = None
    applied_at: Optional[float] = None

    # Compact summary auto-generated from the fields below
    summary: str = ""

    # Structured summary fields
    what_was_done: str = ""
    whats_next: str = ""
    relevant_files: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Handoff":
        d = dict(d)
        d["status"] = HandoffStatus(d.get("status", "pending"))
        refs_raw = d.pop("context_refs", [])
        obj = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        obj.context_refs = [ContextRef.from_dict(r) for r in refs_raw]
        return obj

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def ttl_remaining_s(self) -> float:
        return max(0.0, self.expires_at - time.time())


def _generate_summary(what_was_done: str, whats_next: str, relevant_files: List[str]) -> str:
    """Auto-generate a compact summary from structured fields."""
    parts = []
    if what_was_done:
        parts.append(f"Done: {what_was_done}")
    if whats_next:
        parts.append(f"Next: {whats_next}")
    if relevant_files:
        file_list = ", ".join(relevant_files[:5])
        if len(relevant_files) > 5:
            file_list += f" (+{len(relevant_files) - 5} more)"
        parts.append(f"Files: {file_list}")
    return " | ".join(parts) if parts else "(no summary)"


class HandoffManager:
    """Manage context handoffs between agents."""

    def __init__(self, handoff_dir: Optional[Path] = None):
        self.handoff_dir = Path(handoff_dir) if handoff_dir else DEFAULT_HANDOFF_DIR
        self.handoff_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, handoff_id: str) -> Path:
        return self.handoff_dir / f"{handoff_id}.json"

    def _save(self, handoff: Handoff) -> None:
        self._path_for(handoff.id).write_text(json.dumps(handoff.to_dict(), indent=2))

    def _load(self, handoff_id: str) -> Optional[Handoff]:
        p = self._path_for(handoff_id)
        if not p.exists():
            return None
        try:
            return Handoff.from_dict(json.loads(p.read_text()))
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_handoff(
        self,
        from_agent: str,
        to_agent: str,
        context_refs: Optional[List[ContextRef]] = None,
        what_was_done: str = "",
        whats_next: str = "",
        relevant_files: Optional[List[str]] = None,
        ttl_hours: float = DEFAULT_TTL_HOURS,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Handoff:
        """Create a new handoff and persist it to disk.

        Raises:
            ValueError: if from_agent or to_agent are not registered agents.
        """
        if from_agent not in REGISTERED_AGENTS:
            raise ValueError(
                f"Unknown from_agent '{from_agent}'. Registered: {sorted(REGISTERED_AGENTS)}"
            )
        if to_agent not in REGISTERED_AGENTS:
            raise ValueError(
                f"Unknown to_agent '{to_agent}'. Registered: {sorted(REGISTERED_AGENTS)}"
            )

        refs = context_refs or []
        files = relevant_files or []
        now = time.time()
        handoff = Handoff(
            id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            context_refs=refs,
            status=HandoffStatus.PENDING,
            created_at=now,
            expires_at=now + ttl_hours * 3600,
            what_was_done=what_was_done,
            whats_next=whats_next,
            relevant_files=files,
            summary=_generate_summary(what_was_done, whats_next, files),
            metadata=metadata or {},
        )
        self._save(handoff)
        return handoff

    def receive_handoff(self, handoff_id: str) -> Handoff:
        """Validate context refs and mark handoff as received.

        Raises:
            FileNotFoundError: if handoff_id doesn't exist.
            ValueError: if handoff is expired or already applied.
        """
        handoff = self._load(handoff_id)
        if handoff is None:
            raise FileNotFoundError(f"Handoff '{handoff_id}' not found")

        if handoff.status == HandoffStatus.APPLIED:
            return handoff  # idempotent

        if handoff.is_expired() or handoff.status == HandoffStatus.EXPIRED:
            handoff.status = HandoffStatus.EXPIRED
            self._save(handoff)
            raise ValueError(f"Handoff '{handoff_id}' has expired")

        # Validate file-type refs
        all_valid = True
        for ref in handoff.context_refs:
            if ref.type == "file":
                ref.valid = Path(ref.path).exists()
                if not ref.valid:
                    all_valid = False
            else:
                ref.valid = True  # non-file refs are always valid

        handoff.status = HandoffStatus.RECEIVED if all_valid else HandoffStatus.INVALID
        handoff.received_at = time.time()
        self._save(handoff)
        return handoff

    def apply_handoff(self, handoff_id: str) -> Handoff:
        """Mark handoff as applied and return loaded context.

        The 'applied' status means the receiving agent has loaded the
        context into its working set. This is a logical marker — actual
        context loading is the caller's responsibility.

        Raises:
            FileNotFoundError: if handoff_id doesn't exist.
            ValueError: if handoff is expired or in an invalid state.
        """
        handoff = self._load(handoff_id)
        if handoff is None:
            raise FileNotFoundError(f"Handoff '{handoff_id}' not found")

        if handoff.status == HandoffStatus.APPLIED:
            return handoff  # idempotent

        if handoff.is_expired() or handoff.status == HandoffStatus.EXPIRED:
            handoff.status = HandoffStatus.EXPIRED
            self._save(handoff)
            raise ValueError(f"Handoff '{handoff_id}' has expired")

        if handoff.status == HandoffStatus.INVALID:
            raise ValueError(f"Handoff '{handoff_id}' has invalid refs — cannot apply")

        # Receive first if still pending
        if handoff.status == HandoffStatus.PENDING:
            handoff = self.receive_handoff(handoff_id)
            if handoff.status == HandoffStatus.INVALID:
                raise ValueError(f"Handoff '{handoff_id}' has invalid refs — cannot apply")

        handoff.status = HandoffStatus.APPLIED
        handoff.applied_at = time.time()
        self._save(handoff)
        return handoff

    def expire_stale(self) -> int:
        """Expire all handoffs that have passed their TTL. Returns count expired."""
        expired = 0
        for p in self.handoff_dir.glob("*.json"):
            try:
                h = Handoff.from_dict(json.loads(p.read_text()))
                if (
                    h.status not in (HandoffStatus.APPLIED, HandoffStatus.EXPIRED)
                    and h.is_expired()
                ):
                    h.status = HandoffStatus.EXPIRED
                    p.write_text(json.dumps(h.to_dict(), indent=2))
                    expired += 1
            except Exception:
                pass
        return expired

    def list_handoffs(
        self,
        to_agent: Optional[str] = None,
        from_agent: Optional[str] = None,
        status: Optional[HandoffStatus] = None,
    ) -> List[Handoff]:
        """List handoffs, optionally filtered by agent or status."""
        results = []
        for p in sorted(
            self.handoff_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True
        ):
            try:
                h = Handoff.from_dict(json.loads(p.read_text()))
            except Exception:
                continue
            if to_agent and h.to_agent != to_agent:
                continue
            if from_agent and h.from_agent != from_agent:
                continue
            if status and h.status != status:
                continue
            results.append(h)
        return results

    def get_handoff(self, handoff_id: str) -> Optional[Handoff]:
        """Get a single handoff by ID."""
        return self._load(handoff_id)


# ---------------------------------------------------------------------------
# TokenPak — high-level block container for agent-to-agent context exchange
# ---------------------------------------------------------------------------


@dataclass
class HandoffBlock:
    """A single content block inside a TokenPak.

    Attributes:
        type:     Semantic type label, e.g. "memory", "evidence", "task_state".
        id:       Unique identifier within the pack.
        content:  Text content.
        metadata: Optional key/value metadata.
    """

    type: str
    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "id": self.id,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "HandoffBlock":
        return cls(
            type=d["type"],
            id=d["id"],
            content=d["content"],
            metadata=d.get("metadata", {}),
        )


class TokenPak:
    """A lightweight container of :class:`HandoffBlock` objects.

    Designed for passing structured context between agents.

    Example::

        pack = TokenPak()
        pack.add(HandoffBlock(type="memory", id="task_state", content=state))
        pack.add(HandoffBlock(type="evidence", id="findings", content=research))
        prompt = pack.to_prompt()

    """

    def __init__(self, blocks: Optional[List[HandoffBlock]] = None):
        self._blocks: List[HandoffBlock] = list(blocks or [])

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, block: HandoffBlock) -> "TokenPak":
        """Append a block to the pack. Returns self for chaining."""
        self._blocks.append(block)
        return self

    def remove(self, block_id: str) -> bool:
        """Remove a block by id. Returns True if found and removed."""
        before = len(self._blocks)
        self._blocks = [b for b in self._blocks if b.id != block_id]
        return len(self._blocks) < before

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def get(self, block_id: str) -> Optional[HandoffBlock]:
        """Return the first block with the given id, or None."""
        for b in self._blocks:
            if b.id == block_id:
                return b
        return None

    def blocks_by_type(self, block_type: str) -> List[HandoffBlock]:
        """Return all blocks with the given type."""
        return [b for b in self._blocks if b.type == block_type]

    @property
    def blocks(self) -> List[HandoffBlock]:
        return list(self._blocks)

    def __len__(self) -> int:
        return len(self._blocks)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"blocks": [b.to_dict() for b in self._blocks]}

    @classmethod
    def from_dict(cls, d: dict) -> "TokenPak":
        blocks = [HandoffBlock.from_dict(b) for b in d.get("blocks", [])]
        return cls(blocks=blocks)

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def to_prompt(self) -> str:
        """Render all blocks as a structured prompt string.

        Each block is rendered as::

            === <TYPE> [<id>] ===
            <content>

        """
        if not self._blocks:
            return ""
        parts = []
        for block in self._blocks:
            header = f"=== {block.type.upper()} [{block.id}] ==="
            parts.append(f"{header}\n{block.content}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Wire format for Handoff  — extends Handoff with pack + to_wire/from_wire
# ---------------------------------------------------------------------------


class HandoffWire:
    """JSON-serialisable wire representation of a :class:`Handoff` + :class:`TokenPak`.

    Usage::

        wire_obj = HandoffWire(pack=pack, from_agent="research", to_agent="writer")
        wire_str = wire_obj.to_wire()

        wire_obj2 = HandoffWire.from_wire(wire_str)
        context   = wire_obj2.pack.to_prompt()

    This is intentionally separate from :class:`HandoffManager` (file-based
    persistence) — the wire format is for direct in-process or network passing.
    """

    VERSION = "tokpak-handoff:1"

    def __init__(
        self,
        pack: TokenPak,
        from_agent: str,
        to_agent: str,
        summary: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        handoff_id: Optional[str] = None,
    ):
        self.pack = pack
        self.from_agent = from_agent
        self.to_agent = to_agent
        self.summary = summary
        self.metadata = metadata or {}
        self.id = handoff_id or str(uuid.uuid4())
        self.created_at = time.time()

    def to_wire(self) -> str:
        """Serialise to a JSON string (the "wire" format)."""
        payload = {
            "version": self.VERSION,
            "id": self.id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "summary": self.summary,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "pack": self.pack.to_dict(),
        }
        return json.dumps(payload)

    @classmethod
    def from_wire(cls, wire: str) -> "HandoffWire":
        """Deserialise from JSON wire string.

        Raises:
            ValueError: if the version header is missing or unrecognised.
        """
        try:
            payload = json.loads(wire)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid wire format: {exc}") from exc

        version = payload.get("version", "")
        if not version.startswith("tokpak-handoff:"):
            raise ValueError(f"Unrecognised wire version: {version!r}")

        pack = TokenPak.from_dict(payload.get("pack", {}))
        obj = cls(
            pack=pack,
            from_agent=payload["from_agent"],
            to_agent=payload["to_agent"],
            summary=payload.get("summary", ""),
            metadata=payload.get("metadata", {}),
            handoff_id=payload.get("id"),
        )
        obj.created_at = payload.get("created_at", time.time())
        return obj

    # Convenience alias — from tokenpak import Handoff → Handoff(pack=..., ...)
    to_dict = lambda self: json.loads(self.to_wire())  # noqa: E731
