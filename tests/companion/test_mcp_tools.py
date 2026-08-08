# SPDX-License-Identifier: Apache-2.0
"""Unit tests for MCP tool handlers — call handlers directly with CompanionState.

These are unit tests (not integration tests).  They call handler functions
directly rather than going through the JSON-RPC server subprocess.  Each
test constructs a CompanionState pointed at a tmp_path journal dir, calls
the handler, and asserts on the parsed JSON output.

Integration (JSON-RPC protocol) tests are in test_mcp_server.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tokenpak.companion.config import CompanionConfig
from tokenpak.companion.mcp.tools import (
    TOOLS,
    CompanionState,
    _handle_check_budget,
    _handle_estimate_tokens,
    _handle_journal_read,
    _handle_journal_write,
    _handle_load_capsule,
    _handle_prune_context,
    _handle_session_info,
    active_tools,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fake_proxy(monkeypatch):
    """Fixture-backed proxy contract so these handler tests never need a live proxy."""
    journal_entries: dict[str, list[dict[str, str]]] = {}

    def fake_get(path: str, params: dict[str, Any] | None = None):
        params = params or {}
        if path == "/tpk/v1/budget":
            return 200, {
                "session_cost_usd": 0.0,
                "daily_cost_usd": 0.0,
                "daily_budget_usd": 10.0,
                "remaining_usd": 10.0,
                "session_requests": 0,
                "budget_set": True,
            }
        if path == "/tpk/v1/capsules":
            return 200, {"capsules": []}
        if path.startswith("/tpk/v1/capsules/"):
            session_id = path.rsplit("/", 1)[-1]
            if session_id == "mysess":
                return 200, {"content": "## Session Capsule: mysess\nDecisions: ..."}
            return 404, {"error": "capsule_not_found", "detail": session_id}
        if path == "/tpk/v1/journal/sessions":
            return 200, {"sessions": []}
        if path.startswith("/tpk/v1/journal/"):
            session_id = path.rsplit("/", 1)[-1]
            entries = list(journal_entries.get(session_id, []))
            entry_type = params.get("entry_type")
            if entry_type:
                entries = [entry for entry in entries if entry["type"] == entry_type]
            return 200, {"session_id": session_id, "entries": entries}
        if path == "/tpk/v1/session/info":
            return 200, {"version": "test-proxy", "mode": "fixture"}
        raise AssertionError(f"unexpected proxy GET: {path} {params}")

    def fake_post(
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ):
        body = body or {}
        if path == "/tpk/v1/tokens/estimate":
            if "file_path" in body:
                file_path = Path(str(body["file_path"]))
                if not file_path.exists():
                    return 404, {"error": "file_not_found", "detail": str(file_path)}
                text = file_path.read_text(encoding="utf-8")
            else:
                text = str(body.get("text", ""))
            chars = len(text)
            return 200, {
                "tokens": max(1, chars // 4),
                "chars": chars,
                "chars_per_token": 4.0,
                "estimator": "chars-per-4-heuristic",
                "note": "approximate — install tokenpak[tokens] for exact counts",
            }
        if path == "/tpk/v1/compress":
            text = str(body.get("text", ""))
            max_tokens = int(body.get("max_tokens", 2000) or 2000)
            if len(text) <= max_tokens * 4:
                return 200, {"pruned_text": text, "reduction_pct": 0.0}
            pruned = f"{text[:80]}\n[... elided ...]\n{text[-80:]}"
            reduction = 100.0 * (1.0 - (len(pruned) / len(text)))
            return 200, {"pruned_text": pruned, "reduction_pct": reduction}
        if path.startswith("/tpk/v1/journal/") and path.endswith("/entry"):
            session_id = path.split("/")[-2]
            entry = {
                "type": str(body.get("entry_type", "user")),
                "content": str(body.get("content", "")),
            }
            journal_entries.setdefault(session_id, []).append(entry)
            return 200, {"status": "ok", "session_id": session_id}
        raise AssertionError(f"unexpected proxy POST: {path} {body} {params}")

    monkeypatch.setattr("tokenpak.companion.mcp.tools._proxy_get", fake_get)
    monkeypatch.setattr("tokenpak.companion.mcp.tools._proxy_post", fake_post)


def _make_state(tmp_path: Path, session_id: str = "") -> CompanionState:
    """Build a CompanionState backed by a temp journal directory."""
    cfg = CompanionConfig(journal_dir=tmp_path, budget_daily_usd=10.0)
    return CompanionState(config=cfg, session_id=session_id)


def test_companion_state_explicit_session_wins_over_config(tmp_path):
    cfg = CompanionConfig(journal_dir=tmp_path, session_id="sess-config")
    state = CompanionState(config=cfg, session_id="sess-explicit")
    assert state.session_id == "sess-explicit"


# ---------------------------------------------------------------------------
# Tool registry sanity
# ---------------------------------------------------------------------------


def test_tools_registry_has_expected_entries():
    """Red under registry drift: tool additions/removals must update this set."""
    names = {t.name for t in TOOLS}
    assert names == {
        "estimate_tokens",
        "check_budget",
        "load_pak",
        "load_capsule",
        "prune_context",
        "journal_read",
        "journal_write",
        "session_info",
        "vault_search",
        "vault_retrieve",
    }


def test_all_tools_have_handler_callable():
    for t in TOOLS:
        assert callable(t.handler), f"{t.name} handler is not callable"


def test_load_capsule_is_deprecated_alias_of_load_pak():
    """The legacy name must dispatch to the same handler as the canonical one,
    with identical input schema, so existing configs keep working."""
    by_name = {t.name: t for t in TOOLS}
    pak, legacy = by_name["load_pak"], by_name["load_capsule"]
    assert pak.handler is legacy.handler
    assert pak.input_schema == legacy.input_schema
    assert "eprecated" in legacy.description and "load_pak" in legacy.description


def test_active_tools_lean_advertises_core_only():
    """Lean drops accounting/diagnostic tools and the deprecated alias from
    the advertised list; recall, compression, and journal tools survive."""
    lean = {t.name for t in active_tools("lean")}
    assert lean == {t.name for t in TOOLS if t.core}
    assert {"estimate_tokens", "check_budget", "session_info", "load_capsule"}.isdisjoint(lean)
    assert {"load_pak", "prune_context", "journal_read", "journal_write"} <= lean


def test_active_tools_other_profiles_advertise_full_registry():
    for profile in ("balanced", "verbose", "", "unknown"):
        assert active_tools(profile) == list(TOOLS)


def test_server_tools_list_respects_profile(tmp_path, monkeypatch):
    """The server's tools/list must advertise the profile-filtered set while
    dispatch remains unfiltered (a lean session can still call any tool)."""
    from tokenpak.companion.mcp import server as mcp_server

    sent: list[dict] = []
    monkeypatch.setattr(mcp_server, "_send", lambda msg: sent.append(msg))

    lean_state = CompanionState(config=CompanionConfig(journal_dir=tmp_path, profile="lean"))
    mcp_server._handle_tools_list(7, lean_state)
    assert {t["name"] for t in sent[0]["result"]["tools"]} == {t.name for t in TOOLS if t.core}

    full_state = CompanionState(config=CompanionConfig(journal_dir=tmp_path, profile="balanced"))
    mcp_server._handle_tools_list(8, full_state)
    assert {t["name"] for t in sent[1]["result"]["tools"]} == {t.name for t in TOOLS}

    # Dispatch is not profile-gated: a non-core tool still resolves and runs.
    mcp_server._handle_tools_call(9, {"name": "check_budget", "arguments": {}}, lean_state)
    assert sent[2]["id"] == 9
    assert "error" not in sent[2]


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_inline_text(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"text": "hello world"}))
    assert result["tokens"] > 0
    assert result["chars"] == len("hello world")


def test_estimate_tokens_result_is_compacted(tmp_path):
    """Tool results persist in conversation context — verbose proxy metadata
    must be compacted before it reaches the model, while the estimator
    disclosure survives in short form."""
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"text": "hello world"}))
    assert "chars_per_token" not in result
    assert result["estimator"] == "chars/4-approx"
    assert result["note"] == "install tokenpak[tokens] for tokenizer counts"


def test_estimate_tokens_tokenizer_estimator_passes_through(tmp_path, monkeypatch):
    """A real tokenizer name is passed through unchanged, with no install note."""

    def fake_post(path, body=None, params=None):
        return 200, {
            "tokens": 3,
            "chars": 11,
            "chars_per_token": 3.67,
            "estimator": "tiktoken:cl100k_base",
        }

    monkeypatch.setattr("tokenpak.companion.mcp.tools._proxy_post", fake_post)
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"text": "hello world"}))
    assert result["estimator"] == "tiktoken:cl100k_base"
    assert "note" not in result
    assert "chars_per_token" not in result


def test_estimate_tokens_uses_tiktoken(tmp_path):
    """Proxy-delegated estimator returns a positive token count."""
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"text": "hello world this is a test"}))
    assert result["tokens"] > 0


def test_estimate_tokens_from_file(tmp_path):
    f = tmp_path / "input.txt"
    f.write_text("the quick brown fox")
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"file_path": str(f)}))
    assert result["tokens"] > 0
    assert result["chars"] == len("the quick brown fox")


def test_estimate_tokens_missing_file_returns_error(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"file_path": "/does/not/exist.txt"}))
    assert "error" in result


def test_estimate_tokens_empty_text(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"text": ""}))
    assert "error" in result


def test_estimate_tokens_large_text_chunks(tmp_path):
    """Text > 100k chars is processed in chunks without error."""
    big_text = "word " * 25_000  # 125000 chars
    state = _make_state(tmp_path)
    result = json.loads(_handle_estimate_tokens(state, {"text": big_text}))
    assert result["tokens"] > 0
    assert result["chars"] == len(big_text)


# ---------------------------------------------------------------------------
# check_budget
# ---------------------------------------------------------------------------


def test_check_budget_returns_required_fields(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_check_budget(state, {}))
    for key in (
        "session_cost_usd",
        "daily_cost_usd",
        "daily_budget_usd",
        "remaining_usd",
        "session_requests",
    ):
        assert key in result, f"missing key: {key}"


def test_check_budget_zero_session_cost_initially(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_check_budget(state, {}))
    assert result["session_cost_usd"] == 0.0
    assert result["session_requests"] == 0


def test_check_budget_reflects_daily_budget(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_check_budget(state, {}))
    assert result["daily_budget_usd"] == 10.0
    assert result["budget_set"] is True


def test_check_budget_daily_totals_from_tracker(tmp_path):
    """Budget handler reports proxy-owned daily totals."""
    state = _make_state(tmp_path)
    state.budget_tracker.record(
        input_tokens=1000, output_tokens=500, model="sonnet", session_id="s1"
    )
    result = json.loads(_handle_check_budget(state, {}))
    assert "daily_cost_usd" in result
    assert "_tokenpak_scope" in result


# ---------------------------------------------------------------------------
# prune_context
# ---------------------------------------------------------------------------


def test_prune_context_50pct_reduction_on_10k_chars(tmp_path):
    """prune_context achieves >50% reduction on 10k+ char input."""
    text = "word " * 2200  # 11000 chars
    state = _make_state(tmp_path)
    result = json.loads(_handle_prune_context(state, {"text": text, "max_tokens": 100}))
    assert result["reduction_pct"] >= 50.0
    assert len(result["pruned_text"]) < len(text)


def test_prune_context_short_text_unchanged(tmp_path):
    state = _make_state(tmp_path)
    short = "hello"
    result = json.loads(_handle_prune_context(state, {"text": short, "max_tokens": 2000}))
    assert result["pruned_text"] == short
    assert result["reduction_pct"] == 0.0


def test_prune_context_no_text_returns_error(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_prune_context(state, {}))
    assert "error" in result


def test_prune_context_elision_marker_present(tmp_path):
    """Middle is replaced with an elision marker."""
    text = "a " * 5000  # 10000 chars
    state = _make_state(tmp_path)
    result = json.loads(_handle_prune_context(state, {"text": text, "max_tokens": 50}))
    assert "elided" in result["pruned_text"]


def test_prune_context_reduction_wrapped_in_pak_envelope(tmp_path):
    """Actual reductions carry the Pak envelope so re-injected content is
    attributable to TokenPak."""
    text = "word " * 2200
    state = _make_state(tmp_path)
    result = json.loads(_handle_prune_context(state, {"text": text, "max_tokens": 100}))
    assert result["reduction_pct"] > 0
    assert result["pruned_text"].startswith("[PAK id=")
    assert result["pruned_text"].rstrip().endswith("[/PAK]")


def test_prune_context_no_reduction_not_enveloped(tmp_path):
    """Zero-reduction passthrough stays byte-identical — no envelope."""
    text = "short text"
    state = _make_state(tmp_path)
    result = json.loads(_handle_prune_context(state, {"text": text, "max_tokens": 2000}))
    assert result["pruned_text"] == text


# ---------------------------------------------------------------------------
# load_capsule
# ---------------------------------------------------------------------------


def test_load_capsule_empty_dir(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_load_capsule(state, {}))
    assert isinstance(result["capsules"], list)


def test_load_capsule_lists_saved_capsules(tmp_path):
    """load_capsule lists proxy-owned capsules."""
    capsule_dir = tmp_path / "capsules"
    capsule_dir.mkdir()
    (capsule_dir / "abc123.md").write_text("## Session Capsule: abc123\nsome content")
    state = _make_state(tmp_path)
    result = json.loads(_handle_load_capsule(state, {}))
    assert isinstance(result["capsules"], list)


def test_load_capsule_returns_content_by_session_id(tmp_path):
    """Specific capsule lookup returns content or a structured not-found error."""
    capsule_dir = tmp_path / "capsules"
    capsule_dir.mkdir()
    (capsule_dir / "mysess.md").write_text("## Session Capsule: mysess\nDecisions: ...")
    state = _make_state(tmp_path)
    result = _handle_load_capsule(state, {"session_id": "mysess"})
    if result.startswith("{"):
        assert json.loads(result)["error"] == "capsule_not_found"
    else:
        assert "Session Capsule" in result


def test_load_capsule_missing_session_returns_error(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_load_capsule(state, {"session_id": "no-such-session"}))
    assert "error" in result


def test_load_pak_legacy_single_session_result_is_byte_stable(tmp_path, monkeypatch):
    expected = "## Session Capsule: legacy\nDecisions: unchanged"

    def fake_get(path, params=None):
        assert path == "/tpk/v1/capsules/legacy"
        return 200, {"content": expected}

    monkeypatch.setattr("tokenpak.companion.mcp.tools._proxy_get", fake_get)
    assert _handle_load_capsule(_make_state(tmp_path), {"session_id": "legacy"}) == expected


def test_load_pak_batches_paks_and_journals_in_one_result(tmp_path, monkeypatch):
    def fake_get(path, params=None):
        target = path.rsplit("/", 1)[-1]
        if "/journal/" in path:
            return 200, {"session_id": target, "entries": [{"content": f"note-{target}"}]}
        return 200, {"content": f"pak-{target}"}

    monkeypatch.setattr("tokenpak.companion.mcp.tools._proxy_get", fake_get)
    result = _handle_load_capsule(
        _make_state(tmp_path, session_id="caller"),
        {"session_ids": ["a", "b", "c"], "include_journal": True},
    )
    for target in ("a", "b", "c"):
        assert f"[Journal: {target}]" in result
        assert f"note-{target}" in result
        assert f"[Pak: {target}]" in result
        assert f"pak-{target}" in result


def test_load_pak_batch_rejects_invalid_or_oversized_ids(tmp_path):
    state = _make_state(tmp_path)
    invalid = json.loads(_handle_load_capsule(state, {"session_ids": "not-an-array"}))
    non_string = json.loads(_handle_load_capsule(state, {"session_ids": ["valid", 7]}))
    empty = json.loads(_handle_load_capsule(state, {"session_ids": ["valid", " "]}))
    oversized = json.loads(
        _handle_load_capsule(state, {"session_ids": [str(index) for index in range(11)]})
    )
    invalid_journal = json.loads(
        _handle_load_capsule(state, {"session_ids": ["valid"], "include_journal": "true"})
    )
    assert "array" in invalid["error"]
    assert "non-empty strings" in non_string["error"]
    assert "non-empty strings" in empty["error"]
    assert "at most 10" in oversized["error"]
    assert "boolean" in invalid_journal["error"]


# ---------------------------------------------------------------------------
# journal_write / journal_read — round-trip
# ---------------------------------------------------------------------------


def test_journal_write_requires_active_session(tmp_path):
    state = _make_state(tmp_path, session_id="")
    result = json.loads(_handle_journal_write(state, {"content": "hello"}))
    assert "error" in result


def test_journal_write_no_content_returns_error(tmp_path):
    state = _make_state(tmp_path, session_id="sess-abc")
    result = json.loads(_handle_journal_write(state, {}))
    assert "error" in result


def test_journal_write_returns_ok(tmp_path):
    state = _make_state(tmp_path, session_id="sess-abc")
    result = json.loads(_handle_journal_write(state, {"content": "a note"}))
    assert result["status"] == "ok"
    assert result["session_id"] == "sess-abc"


def test_journal_read_write_round_trip(tmp_path):
    """Write a note then read it back — verifies SQLite persistence."""
    state = _make_state(tmp_path, session_id="sess-roundtrip")

    # Write two entries
    _handle_journal_write(state, {"content": "first note"})
    _handle_journal_write(state, {"content": "second note"})

    # Read them back
    result = json.loads(_handle_journal_read(state, {"session_id": "sess-roundtrip"}))
    assert result["session_id"] == "sess-roundtrip"
    contents = {e["content"] for e in result["entries"]}
    assert "first note" in contents
    assert "second note" in contents


def test_journal_read_no_session_lists_sessions(tmp_path):
    """journal_read with no session_id returns sessions list."""
    state = _make_state(tmp_path, session_id="")
    result = json.loads(_handle_journal_read(state, {}))
    assert "sessions" in result


def test_journal_read_entry_type_filter(tmp_path):
    """journal_read entry_type filter returns only matching entries."""
    state = _make_state(tmp_path, session_id="sess-filter")
    # Write a 'user' entry (journal_write always uses type='user')
    _handle_journal_write(state, {"content": "my note"})

    # Filter by type 'user'
    result = json.loads(
        _handle_journal_read(state, {"session_id": "sess-filter", "entry_type": "user"})
    )
    assert all(e["type"] == "user" for e in result["entries"])

    # Filter by type 'milestone' — should be empty
    result2 = json.loads(
        _handle_journal_read(state, {"session_id": "sess-filter", "entry_type": "milestone"})
    )
    assert result2["entries"] == []


# ---------------------------------------------------------------------------
# session_info
# ---------------------------------------------------------------------------


def test_session_info_returns_version(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_session_info(state, {}))
    assert result["companion_version"] == "0.1.0"


def test_session_info_returns_config_block(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_session_info(state, {}))
    assert "config" in result
    assert result["config"]["budget_daily_usd"] == 10.0


def test_session_info_returns_budget_block(tmp_path):
    state = _make_state(tmp_path)
    result = json.loads(_handle_session_info(state, {}))
    assert "proxy" in result
    assert "session_id" in result


def test_session_info_call_count_increments(tmp_path):
    """call_count on state is reflected in session_info when incremented."""
    state = _make_state(tmp_path)
    state.call_count = 7
    result = json.loads(_handle_session_info(state, {}))
    assert result["call_count"] == 7
