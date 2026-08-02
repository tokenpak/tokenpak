# SPDX-License-Identifier: Apache-2.0
"""TIP Pak contract — Portable AI Knowledge bundle schema.

A ``Pak`` is a portable, AI-ready knowledge bundle that can be injected
into prompts, handed off across sessions, or shared across agents. This
module defines the TIP-1.x schema for Paks; it is the OSS-side contract
that the MultiPak Pro daemon (closed-source) consumes.

The schema follows the MultiPak Pro architecture (taxonomy) and
the PRD. It must land in OSS before any Pro implementation can rely on
it.

**Subtype taxonomy**:

- ``vault``: long-term durable Pak from project files (authority: file_source).
- ``interaction``: from AI sessions (user prompts, LLM responses, tool outputs).
- ``decision``: promoted authoritative Pak (authority: user_approved).
- ``recall``: temporary retrieval bundle for the current request.
- ``handoff``: target-specific package for another tool/platform.

The deprecated subtype names (``project``, ``memory``, ``context``) are
preserved as import-time aliases until v3.0.0 with a ``DeprecationWarning``.

**Privacy contract**: ``Pak`` instances are local-only. The
license-validation egress path MUST NOT receive any field
defined here; this is enforced by the privacy contract tests.

Naming note: in code we use the title-case form ``Pak`` per the glossary.
The all-caps form ``PAK`` is reserved for marketing-brand stylization
within MultiPak Pro copy and for the acronym definition
(``PAK = Portable AI Knowledge``).
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

# ---------------------------------------------------------------------------
# Subtype, authority, status, confidence, retention enums
# ---------------------------------------------------------------------------


# Module-level alias table — kept outside ``PakSubtype`` to sidestep Enum
# metaclass quirks (a dict assigned inside an Enum body would be treated
# as a member). Maps deprecated legacy subtype names to the
# canonical taxonomy names. Target removal: v3.0.0.
_LEGACY_SUBTYPE_ALIASES: Mapping[str, str] = {
    "project": "vault",
    "context": "recall",
    # ``memory`` resolves to ``interaction`` by default; promotion-time
    # callers should re-tag as ``decision`` when authority warrants.
    "memory": "interaction",
}


class PakSubtype(str, Enum):
    """Canonical Pak subtype taxonomy.

    The 5 values are the canonical taxonomy. Receivers parsing a
    Pak with an unknown subtype string MUST fall back gracefully (per the
    capability-codes rule); never raise on an unrecognized value.
    Use :func:`PakSubtype.parse` to normalize legacy/aliased values.
    """

    VAULT = "vault"
    INTERACTION = "interaction"
    DECISION = "decision"
    RECALL = "recall"
    HANDOFF = "handoff"

    @classmethod
    def parse(cls, value: str) -> "PakSubtype":
        """Parse a subtype string, resolving deprecated aliases with a warning.

        Unknown values raise ``ValueError`` only after the alias table is
        consulted; new subtypes added in future minor revisions of TIP-1.x
        SHOULD be added to this enum and the registry catalog in lockstep
        (no hardcoded enumeration in consumer code paths; discovery stays
        dynamic via the registry catalog).
        """
        normalized = value.strip().lower()
        if normalized in _LEGACY_SUBTYPE_ALIASES:
            canonical = _LEGACY_SUBTYPE_ALIASES[normalized]
            warnings.warn(
                f"Pak subtype {value!r} is a deprecated alias for "
                f"{canonical!r}; use the canonical name. Removal: v3.0.0.",
                DeprecationWarning,
                stacklevel=2,
            )
            return cls(canonical)
        return cls(normalized)


class PakAuthority(str, Enum):
    """Source-of-truth weight applied to a Pak in recall ranking.

    ``user_approved`` outranks ``file_source`` outranks ``tool_result``
    outranks ``llm_generated``. Recall scoring multiplies by this weight.
    """

    USER_APPROVED = "user_approved"
    FILE_SOURCE = "file_source"
    TOOL_RESULT = "tool_result"
    LLM_GENERATED = "llm_generated"


class PakStatus(str, Enum):
    """Lifecycle state of a Pak."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    CONFLICTED = "conflicted"


class PakConfidence(str, Enum):
    """Confidence in the Pak's content. Drives hydration triggering."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PakRetention(str, Enum):
    """TTL bucket for a Pak. Drives compaction.

    ``session`` = lifetime of the originating session.
    ``days_30`` / ``days_180`` = bucketed TTL.
    ``persistent`` = retained until explicitly deleted (Decision Paks default here).
    ``source_lifetime`` = retained as long as the source file/repo exists
    (Vault Paks default here).
    """

    SESSION = "session"
    DAYS_30 = "days_30"
    DAYS_180 = "days_180"
    PERSISTENT = "persistent"
    SOURCE_LIFETIME = "source_lifetime"


class PakSourceType(str, Enum):
    """How the Pak was originally produced."""

    LLM_RESPONSE = "llm_response"
    FILE = "file"
    TOOL_RESULT = "tool_result"
    CODE = "code"
    MANUAL = "manual"


class PakPrivacyClass(str, Enum):
    """Privacy classification. v1 admits ``local_only`` only.

    Additional classes (``team_local`` for Pro Team LAN sharing) require
    their own compatibility review.
    """

    LOCAL_ONLY = "local_only"


# ---------------------------------------------------------------------------
# Sub-records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PakScope:
    """User / project / topic scoping (``project_scope``, ``topic_scope``).

    Cross-project leakage is blocked by default — recall hard-filters on
    ``project`` when an explicit project_scope is declared by the caller.
    """

    user: Optional[str] = None
    project: Optional[str] = None
    topic: Optional[str] = None


@dataclass(frozen=True)
class PakSource:
    """Provenance of the Pak content."""

    platform: str
    source_type: PakSourceType
    created_at: str  # ISO-8601 timestamp; downstream parsers use stdlib datetime.
    source_hash: str  # SHA-256 of the original source bytes.


@dataclass(frozen=True)
class PakAnchor:
    """A content-addressed reference to exact source text.

    Hydration restores the snippet bytes from the anchor store at
    ``~/.tokenpak/pro/state/multipak/anchors/<source_hash>``. ``snippet_available``
    is False when the anchor has been pruned or when the daemon is absent.
    """

    anchor_id: str
    source_hash: str
    snippet_available: bool = True


@dataclass(frozen=True)
class PakRelationships:
    """Directed relationships between Paks (conflict_penalty).

    All four lists hold opaque Pak IDs.
    """

    depends_on: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    related: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()


@dataclass(frozen=True)
class PakRetentionPolicy:
    """Retention configuration."""

    ttl: PakRetention


@dataclass(frozen=True)
class PakPrivacy:
    """Privacy classification."""

    class_: PakPrivacyClass = PakPrivacyClass.LOCAL_ONLY


# ---------------------------------------------------------------------------
# Pak (top-level)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pak:
    """A Portable AI Knowledge bundle.

    Frozen by design — Paks are immutable once captured; mutations create a
    new Pak with ``supersedes`` pointing at the predecessor. This matches
    the recall ranking model where ``conflict_penalty`` and
    ``stale_penalty`` apply to Paks superseded by newer revisions.

    See ``pak-v1.json`` (registry) for the JSON Schema canonical form.
    """

    pak_id: str
    pak_type: PakSubtype
    title: str
    summary: str
    scope: PakScope
    source: PakSource
    status: PakStatus
    authority: PakAuthority
    confidence: PakConfidence
    retention: PakRetentionPolicy
    privacy: PakPrivacy = field(default_factory=PakPrivacy)
    anchors: tuple[PakAnchor, ...] = ()
    relationships: PakRelationships = field(default_factory=PakRelationships)

    # ---- Round-trip ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Render to a JSON-serializable dict matching the wire schema.

        Enum values render as their ``.value`` strings; ``privacy.class_``
        renders as ``privacy.class`` per the wire schema (the trailing underscore
        is a Python keyword-collision workaround, not part of the wire shape).
        """

        def _enum(v: Any) -> Any:
            return v.value if isinstance(v, Enum) else v

        d = asdict(self)
        d["pak_type"] = _enum(self.pak_type)
        d["status"] = _enum(self.status)
        d["authority"] = _enum(self.authority)
        d["confidence"] = _enum(self.confidence)
        d["retention"]["ttl"] = _enum(self.retention.ttl)
        d["source"]["source_type"] = _enum(self.source.source_type)
        d["privacy"] = {"class": self.privacy.class_.value}
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Pak":
        """Parse a Pak from its wire form. Unknown enum values raise ``ValueError``;
        deprecated subtype aliases are normalized via :meth:`PakSubtype.parse`."""

        scope_d = data.get("scope") or {}
        source_d = data["source"]
        privacy_d = data.get("privacy") or {}
        retention_d = data.get("retention") or {}
        relationships_d = data.get("relationships") or {}
        anchors = tuple(PakAnchor(**a) for a in (data.get("anchors") or ()))

        return cls(
            pak_id=data["pak_id"],
            pak_type=PakSubtype.parse(data["pak_type"]),
            title=data["title"],
            summary=data["summary"],
            scope=PakScope(**scope_d),
            source=PakSource(
                platform=source_d["platform"],
                source_type=PakSourceType(source_d["source_type"]),
                created_at=source_d["created_at"],
                source_hash=source_d["source_hash"],
            ),
            status=PakStatus(data["status"]),
            authority=PakAuthority(data["authority"]),
            confidence=PakConfidence(data["confidence"]),
            retention=PakRetentionPolicy(ttl=PakRetention(retention_d.get("ttl", "session"))),
            privacy=PakPrivacy(class_=PakPrivacyClass(privacy_d.get("class", "local_only"))),
            anchors=anchors,
            relationships=PakRelationships(
                depends_on=tuple(relationships_d.get("depends_on", ())),
                supersedes=tuple(relationships_d.get("supersedes", ())),
                related=tuple(relationships_d.get("related", ())),
                conflicts_with=tuple(relationships_d.get("conflicts_with", ())),
            ),
        )


# ---------------------------------------------------------------------------
# Default-retention discovery (no hardcoded subtype enumeration in consumer
# paths; consumers ask the contract).
# ---------------------------------------------------------------------------


_DEFAULT_RETENTION_BY_SUBTYPE: Mapping[PakSubtype, PakRetention] = {
    PakSubtype.VAULT: PakRetention.SOURCE_LIFETIME,
    PakSubtype.INTERACTION: PakRetention.DAYS_180,
    PakSubtype.DECISION: PakRetention.PERSISTENT,
    PakSubtype.RECALL: PakRetention.SESSION,
    PakSubtype.HANDOFF: PakRetention.DAYS_30,  # PRD: handoff_pak_retention_days: 90
    # NOTE: the PRD lists 90 days for handoff; the 30-day bucket is the
    # nearest enum value. Future revisions may add `DAYS_90` to PakRetention
    # — don't read the bucket as the literal day count.
}


def default_retention_for(subtype: PakSubtype) -> PakRetention:
    """Return the default retention bucket for a Pak subtype.

    Callers MUST consult this function rather than hardcoding subtype-to-
    retention mapping at call sites — when a new subtype is added in a
    future TIP-1.x minor revision, only this table needs updating.
    """
    if subtype not in _DEFAULT_RETENTION_BY_SUBTYPE:
        # Receivers fall back gracefully on unknown subtypes.
        return PakRetention.SESSION
    return _DEFAULT_RETENTION_BY_SUBTYPE[subtype]


def all_subtypes() -> Iterable[PakSubtype]:
    """Iterate the canonical Pak subtypes. Deprecated aliases are NOT included."""
    return iter(PakSubtype)


__all__ = [
    "Pak",
    "PakAnchor",
    "PakAuthority",
    "PakConfidence",
    "PakPrivacy",
    "PakPrivacyClass",
    "PakRelationships",
    "PakRetention",
    "PakRetentionPolicy",
    "PakScope",
    "PakSource",
    "PakSourceType",
    "PakStatus",
    "PakSubtype",
    "all_subtypes",
    "default_retention_for",
]
