# SPDX-License-Identifier: Apache-2.0
"""``RecallStore.list_paks`` and ``RecallStore.get_pak`` — read coverage.

Storage-level tests for the OSS read surface introduced alongside the
``/pak/v1/list`` endpoint. The HTTP layer has its own coverage; this
module verifies the SQL-level behaviour the endpoint depends on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tokenpak.companion.recall import (
    LIST_LIMIT_DEFAULT,
    LIST_LIMIT_MAX,
    PakListFilters,
    PakRow,
    RecallStore,
)


def _seed(store: RecallStore, *rows: dict[str, Any]) -> None:
    """Insert each row via ``upsert_pak``; ``now`` controls ordering."""
    for r in rows:
        store.upsert_pak(**r)


def _row(
    pak_id: str,
    *,
    now: str,
    pak_type: str = "vault",
    project: str | None = "alpha",
    source_type: str = "doc",
    authority: str = "llm_generated",
    title: str | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "pak_id": pak_id,
        "pak_type": pak_type,
        "source_type": source_type,
        "authority": authority,
        "title": title or f"title-{pak_id}",
        "content_hash": content_hash or (pak_id * 4)[:32],
        "project": project,
        "now": now,
    }


# ---------------------------------------------------------------------------
# list_paks — empty / basic / ordering
# ---------------------------------------------------------------------------


def test_list_empty_store(tmp_path: Path, require_fts5: None) -> None:
    """Listing an empty store yields zero items, no cursor, not truncated."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        result = store.list_paks()

    assert result.items == []
    assert result.next_cursor is None
    assert result.truncated is False
    assert result.limit == LIST_LIMIT_DEFAULT


def test_list_returns_rows_newest_first(tmp_path: Path, require_fts5: None) -> None:
    """Rows are ordered by ``updated_at DESC, pak_id DESC``."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a", now="2026-05-11T10:00:00Z"),
            _row("b", now="2026-05-11T10:02:00Z"),
            _row("c", now="2026-05-11T10:01:00Z"),
        )
        result = store.list_paks()

    assert [r.pak_id for r in result.items] == ["b", "c", "a"]
    assert all(isinstance(r, PakRow) for r in result.items)
    assert result.truncated is False
    assert result.next_cursor is None


def test_list_tiebreak_by_pak_id_desc(tmp_path: Path, require_fts5: None) -> None:
    """Within the same ``updated_at`` rows tiebreak by ``pak_id DESC``."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a1", now="2026-05-11T10:00:00Z"),
            _row("a2", now="2026-05-11T10:00:00Z"),
            _row("a3", now="2026-05-11T10:00:00Z"),
        )
        result = store.list_paks()

    assert [r.pak_id for r in result.items] == ["a3", "a2", "a1"]


# ---------------------------------------------------------------------------
# list_paks — filters
# ---------------------------------------------------------------------------


def test_filter_by_pak_type_byte_literal(tmp_path: Path, require_fts5: None) -> None:
    """``pak_type`` filter is byte-literal — no alias / casefold."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a", now="2026-05-11T10:00:00Z", pak_type="vault"),
            _row("b", now="2026-05-11T10:01:00Z", pak_type="interaction"),
            _row("c", now="2026-05-11T10:02:00Z", pak_type="vault"),
            _row("d", now="2026-05-11T10:03:00Z", pak_type="decision"),
        )

        only_vault = store.list_paks(PakListFilters(pak_type="vault"))
        only_interaction = store.list_paks(PakListFilters(pak_type="interaction"))
        # Alias check — the legacy "project" name is NOT translated to "vault"
        # by this layer. The filter matches literally; zero rows have that value.
        alias_attempt = store.list_paks(PakListFilters(pak_type="project"))
        # Casefold check — "VAULT" does not match "vault" byte-literally.
        casefold_attempt = store.list_paks(PakListFilters(pak_type="VAULT"))

    assert [r.pak_id for r in only_vault.items] == ["c", "a"]
    assert [r.pak_id for r in only_interaction.items] == ["b"]
    assert alias_attempt.items == []
    assert casefold_attempt.items == []


def test_filter_by_project_byte_literal(tmp_path: Path, require_fts5: None) -> None:
    """``project`` filter is byte-literal too."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a", now="2026-05-11T10:00:00Z", project="alpha"),
            _row("b", now="2026-05-11T10:01:00Z", project="beta"),
            _row("c", now="2026-05-11T10:02:00Z", project="alpha"),
            _row("d", now="2026-05-11T10:03:00Z", project=None),
        )

        only_alpha = store.list_paks(PakListFilters(project="alpha"))
        only_beta = store.list_paks(PakListFilters(project="beta"))

    assert [r.pak_id for r in only_alpha.items] == ["c", "a"]
    assert [r.pak_id for r in only_beta.items] == ["b"]


def test_filters_compose_with_and(tmp_path: Path, require_fts5: None) -> None:
    """Multiple filters compose with logical AND."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a", now="2026-05-11T10:00:00Z", pak_type="vault", project="alpha"),
            _row("b", now="2026-05-11T10:01:00Z", pak_type="vault", project="beta"),
            _row("c", now="2026-05-11T10:02:00Z", pak_type="interaction", project="alpha"),
        )
        result = store.list_paks(PakListFilters(pak_type="vault", project="alpha"))

    assert [r.pak_id for r in result.items] == ["a"]


# ---------------------------------------------------------------------------
# list_paks — limit, cap, truncation
# ---------------------------------------------------------------------------


def test_default_limit_is_default(tmp_path: Path, require_fts5: None) -> None:
    """The default ``PakListFilters`` reports the default limit in the result."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        result = store.list_paks()
    assert result.limit == LIST_LIMIT_DEFAULT


def test_limit_clamps_to_max(tmp_path: Path, require_fts5: None) -> None:
    """A caller asking for more than the max is silently clamped."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        result = store.list_paks(PakListFilters(limit=999_999))
    assert result.limit == LIST_LIMIT_MAX
    # Defensive: the cap is 100 today; if that changes, this assertion shows it.
    assert result.limit == 100


def test_non_positive_limit_falls_back_to_default(tmp_path: Path, require_fts5: None) -> None:
    """Zero or negative limits at the API edge default; the HTTP layer rejects."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        result_zero = store.list_paks(PakListFilters(limit=0))
        result_neg = store.list_paks(PakListFilters(limit=-5))
    assert result_zero.limit == LIST_LIMIT_DEFAULT
    assert result_neg.limit == LIST_LIMIT_DEFAULT


def test_truncation_flag_and_cursor(tmp_path: Path, require_fts5: None) -> None:
    """When more rows match than the page returns, truncated=True + cursor set."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a", now="2026-05-11T10:00:00Z"),
            _row("b", now="2026-05-11T10:01:00Z"),
            _row("c", now="2026-05-11T10:02:00Z"),
            _row("d", now="2026-05-11T10:03:00Z"),
        )
        page1 = store.list_paks(PakListFilters(limit=2))

    assert [r.pak_id for r in page1.items] == ["d", "c"]
    assert page1.truncated is True
    assert page1.next_cursor is not None
    assert page1.limit == 2


def test_cursor_resumes_at_next_row(tmp_path: Path, require_fts5: None) -> None:
    """Passing back ``next_cursor`` yields the rows the previous page did not."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(
            store,
            _row("a", now="2026-05-11T10:00:00Z"),
            _row("b", now="2026-05-11T10:01:00Z"),
            _row("c", now="2026-05-11T10:02:00Z"),
            _row("d", now="2026-05-11T10:03:00Z"),
        )
        page1 = store.list_paks(PakListFilters(limit=2))
        page2 = store.list_paks(PakListFilters(limit=2, cursor=page1.next_cursor))

    assert [r.pak_id for r in page1.items] == ["d", "c"]
    assert [r.pak_id for r in page2.items] == ["b", "a"]
    assert page2.truncated is False
    assert page2.next_cursor is None


def test_invalid_cursor_raises_value_error(tmp_path: Path, require_fts5: None) -> None:
    """A malformed cursor surfaces as ``ValueError`` — the HTTP layer maps to 400."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        with pytest.raises(ValueError):
            store.list_paks(PakListFilters(cursor="not-a-real-cursor!!!"))


# ---------------------------------------------------------------------------
# get_pak
# ---------------------------------------------------------------------------


def test_get_pak_hit(tmp_path: Path, require_fts5: None) -> None:
    """``get_pak`` returns a fully-populated :class:`PakRow` on hit."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        _seed(store, _row("a", now="2026-05-11T10:00:00Z", project="alpha"))
        row = store.get_pak("a")

    assert row is not None
    assert isinstance(row, PakRow)
    assert row.pak_id == "a"
    assert row.project == "alpha"
    assert row.pak_type == "vault"


def test_get_pak_miss(tmp_path: Path, require_fts5: None) -> None:
    """``get_pak`` returns ``None`` when the row does not exist."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        assert store.get_pak("never-inserted") is None


def test_get_pak_empty_inputs_are_misses(tmp_path: Path, require_fts5: None) -> None:
    """Empty / whitespace ``pak_id`` short-circuits to ``None`` without a SELECT."""
    with RecallStore.open(tmp_path / "recall.db") as store:
        assert store.get_pak("") is None
        assert store.get_pak("   ") is None
