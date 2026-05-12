# SPDX-License-Identifier: Apache-2.0
"""DDL for the recall storage foundation.

This module is intentionally pure data: no I/O, no connections, no logic.
Migrations import these constants; the migration runner is the only writer.

Schema shape:
- ``schema_version`` — single-row pin for the migration runner.
- ``paks``           — metadata index for Pak records (no full content).
- ``paks_fts``       — FTS5 virtual table over title + summary.
- ``paks_fts`` triggers (v2) — ``AFTER INSERT|UPDATE|DELETE`` on ``paks``
                       keep the FTS shadow in lockstep with the metadata
                       row. The triggers are the only write-side contract
                       between ``paks`` and ``paks_fts``.
- ``pak_anchors``    — anchor refs into source files / symbols / URLs.
- ``pak_relations``  — supersession + dependency edges.

Privacy: full Pak content lives outside this index. Only metadata, a
short title, and a short summary may sit here. The FTS index is
external-content-free for the same reason.

References (architecture):
    - MultiPak Pro Architecture standard, sections on the recall model,
      context delivery levels, phasing, and Decision #9.
    - Pro Tier Architecture standard, section 1.1: TIP capabilities must
      land in the open-source surface before the Pro-tier daemon can
      consume them.
"""

from __future__ import annotations

from typing import Final

SCHEMA_VERSION: Final[int] = 3
"""Latest schema version applied by the current code."""


SQL_CREATE_SCHEMA_VERSION: Final[str] = """
CREATE TABLE IF NOT EXISTS schema_version (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    version      INTEGER NOT NULL,
    applied_at   TEXT    NOT NULL
)
""".strip()


SQL_CREATE_PAKS: Final[str] = """
CREATE TABLE IF NOT EXISTS paks (
    pak_id           TEXT    PRIMARY KEY,
    pak_type         TEXT    NOT NULL,
    project          TEXT,
    topic            TEXT,
    source_type      TEXT    NOT NULL,
    authority        TEXT    NOT NULL,
    title            TEXT    NOT NULL,
    summary          TEXT    NOT NULL DEFAULT '',
    content_hash     TEXT    NOT NULL,
    created_at       TEXT    NOT NULL,
    updated_at       TEXT    NOT NULL,
    superseded_by    TEXT REFERENCES paks(pak_id) ON DELETE SET NULL
)
""".strip()


SQL_CREATE_PAKS_FTS: Final[str] = """
CREATE VIRTUAL TABLE IF NOT EXISTS paks_fts USING fts5(
    pak_id UNINDEXED,
    title,
    summary,
    tokenize='unicode61 remove_diacritics 2'
)
""".strip()


SQL_CREATE_PAK_ANCHORS: Final[str] = """
CREATE TABLE IF NOT EXISTS pak_anchors (
    pak_id        TEXT NOT NULL REFERENCES paks(pak_id) ON DELETE CASCADE,
    anchor_id     TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    kind          TEXT NOT NULL,
    PRIMARY KEY (pak_id, anchor_id)
)
""".strip()


SQL_CREATE_PAK_RELATIONS: Final[str] = """
CREATE TABLE IF NOT EXISTS pak_relations (
    pak_id          TEXT NOT NULL REFERENCES paks(pak_id) ON DELETE CASCADE,
    related_pak_id  TEXT NOT NULL REFERENCES paks(pak_id) ON DELETE CASCADE,
    relation_type   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (pak_id, related_pak_id, relation_type)
)
""".strip()


SQL_CREATE_INDEXES: Final[tuple[str, ...]] = (
    "CREATE INDEX IF NOT EXISTS idx_paks_project       ON paks(project)",
    "CREATE INDEX IF NOT EXISTS idx_paks_topic         ON paks(topic)",
    "CREATE INDEX IF NOT EXISTS idx_paks_pak_type      ON paks(pak_type)",
    "CREATE INDEX IF NOT EXISTS idx_paks_updated       ON paks(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_paks_content_hash  ON paks(content_hash)",
    "CREATE INDEX IF NOT EXISTS idx_pak_anchors_source ON pak_anchors(source_path)",
    "CREATE INDEX IF NOT EXISTS idx_relations_related  ON pak_relations(related_pak_id)",
)


ALL_DDL_V1: Final[tuple[str, ...]] = (
    SQL_CREATE_SCHEMA_VERSION,
    SQL_CREATE_PAKS,
    SQL_CREATE_PAKS_FTS,
    SQL_CREATE_PAK_ANCHORS,
    SQL_CREATE_PAK_RELATIONS,
    *SQL_CREATE_INDEXES,
)
"""All statements that make up the v1 schema, applied in order.

Each statement is independently idempotent (``IF NOT EXISTS``), so a
partial application is recoverable by re-running the same list.
"""


EXPECTED_TABLES_V1: Final[frozenset[str]] = frozenset(
    {
        "schema_version",
        "paks",
        "paks_fts",
        "pak_anchors",
        "pak_relations",
    }
)
"""Tables (and the FTS5 virtual table) that must exist after v1 is applied."""


EXPECTED_INDEXES_V1: Final[frozenset[str]] = frozenset(
    {
        "idx_paks_project",
        "idx_paks_topic",
        "idx_paks_pak_type",
        "idx_paks_updated",
        "idx_paks_content_hash",
        "idx_pak_anchors_source",
        "idx_relations_related",
    }
)
"""Named indexes that must exist after v1 is applied."""


# v2 — FTS shadow triggers ----------------------------------------------------
#
# The v2 migration adds three ``AFTER`` triggers on ``paks`` that keep the
# ``paks_fts`` shadow table consistent with the metadata row. Only ``title``
# and ``summary`` are mirrored — those are the only FTS columns.
#
# The ``UPDATE`` trigger is filtered to ``OF title, summary`` so cosmetic
# updates (``updated_at`` alone, or unrelated columns like ``project``)
# don't rewrite the FTS row needlessly.

SQL_CREATE_PAKS_AI_FTS_TRIGGER: Final[str] = """
CREATE TRIGGER IF NOT EXISTS paks_ai_fts
AFTER INSERT ON paks BEGIN
    INSERT INTO paks_fts (pak_id, title, summary)
    VALUES (NEW.pak_id, NEW.title, COALESCE(NEW.summary, ''));
END
""".strip()


SQL_CREATE_PAKS_AU_FTS_TRIGGER: Final[str] = """
CREATE TRIGGER IF NOT EXISTS paks_au_fts
AFTER UPDATE OF title, summary ON paks BEGIN
    DELETE FROM paks_fts WHERE pak_id = OLD.pak_id;
    INSERT INTO paks_fts (pak_id, title, summary)
    VALUES (NEW.pak_id, NEW.title, COALESCE(NEW.summary, ''));
END
""".strip()


SQL_CREATE_PAKS_AD_FTS_TRIGGER: Final[str] = """
CREATE TRIGGER IF NOT EXISTS paks_ad_fts
AFTER DELETE ON paks BEGIN
    DELETE FROM paks_fts WHERE pak_id = OLD.pak_id;
END
""".strip()


ALL_DDL_V2_TRIGGERS: Final[tuple[str, ...]] = (
    SQL_CREATE_PAKS_AI_FTS_TRIGGER,
    SQL_CREATE_PAKS_AU_FTS_TRIGGER,
    SQL_CREATE_PAKS_AD_FTS_TRIGGER,
)
"""Statements introduced by the v2 migration (FTS shadow triggers).

Each statement uses ``IF NOT EXISTS`` so re-application is safe."""


EXPECTED_TRIGGERS_V2: Final[frozenset[str]] = frozenset(
    {
        "paks_ai_fts",
        "paks_au_fts",
        "paks_ad_fts",
    }
)
"""Named triggers that must exist after v2 is applied."""


# v3 — Reason-code + risk-flag join tables ------------------------------------
#
# Two many-to-many join tables that surface per-Pak reason codes and risk
# flags. The codes / flags are registry-defined enums (see the sibling
# ``tokenpak/registry`` repo, ``schemas/tip/pak-reason-codes-v1.schema.json``
# and ``schemas/tip/pak-risk-flags-v1.schema.json``). The runtime does not
# enforce the enum here — validators and the registry schema do — so the
# table accepts any non-empty string and stays additive when new codes /
# flags land in a later Class B amendment.
#
# Intentionally NOT added in this migration: any column on ``paks``, any
# FTS5 index over reason_codes / risk_flags, any /pak/v1/list filtering by
# reason or flag. Those would be a Pro-tier scoring surface (Std 32 §5.2)
# and stay deferred to Pro Phase 2.
#
# References:
#     - Std 32 §5.4 (reason-code registry) — codes catalogue.
#     - Std 32 §5.5 (risk-flag registry) — flags catalogue + severity.
#     - Std 25 §1.1 — TIP capabilities (incl. these join tables) must land
#       in OSS before the Pro daemon can JOIN against them.

SQL_CREATE_PAK_REASON_CODES: Final[str] = """
CREATE TABLE IF NOT EXISTS pak_reason_codes (
    pak_id       TEXT NOT NULL REFERENCES paks(pak_id) ON DELETE CASCADE,
    reason_code  TEXT NOT NULL,
    weight       REAL NOT NULL DEFAULT 1.0 CHECK (weight BETWEEN 0.0 AND 1.0),
    created_at   TEXT NOT NULL,
    PRIMARY KEY (pak_id, reason_code)
)
""".strip()


SQL_CREATE_PAK_RISK_FLAGS: Final[str] = """
CREATE TABLE IF NOT EXISTS pak_risk_flags (
    pak_id       TEXT NOT NULL REFERENCES paks(pak_id) ON DELETE CASCADE,
    risk_flag    TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('info', 'warn', 'block')),
    created_at   TEXT NOT NULL,
    PRIMARY KEY (pak_id, risk_flag)
)
""".strip()


SQL_CREATE_V3_INDEXES: Final[tuple[str, ...]] = (
    "CREATE INDEX IF NOT EXISTS idx_pak_reason_codes_code     ON pak_reason_codes(reason_code)",
    "CREATE INDEX IF NOT EXISTS idx_pak_risk_flags_flag       ON pak_risk_flags(risk_flag)",
    "CREATE INDEX IF NOT EXISTS idx_pak_risk_flags_severity   ON pak_risk_flags(severity)",
)


ALL_DDL_V3: Final[tuple[str, ...]] = (
    SQL_CREATE_PAK_REASON_CODES,
    SQL_CREATE_PAK_RISK_FLAGS,
    *SQL_CREATE_V3_INDEXES,
)
"""Statements introduced by the v3 migration (reason-code + risk-flag join tables)."""


EXPECTED_TABLES_V3: Final[frozenset[str]] = frozenset(
    {
        "pak_reason_codes",
        "pak_risk_flags",
    }
)
"""Tables that must exist after v3 is applied (in addition to v1/v2)."""


EXPECTED_INDEXES_V3: Final[frozenset[str]] = frozenset(
    {
        "idx_pak_reason_codes_code",
        "idx_pak_risk_flags_flag",
        "idx_pak_risk_flags_severity",
    }
)
"""Named indexes that must exist after v3 is applied."""
