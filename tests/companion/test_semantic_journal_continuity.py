# SPDX-License-Identifier: Apache-2.0
"""Semantic journal records survive the supported write and retrieval path."""

from __future__ import annotations

import io
import json
from typing import Any
from urllib.parse import unquote

import pytest

from tokenpak.companion.config import CompanionConfig
from tokenpak.companion.mcp import tools as mcp_tools
from tokenpak.companion.mcp.tools import CompanionState
from tokenpak.proxy import app_endpoints


class _EndpointHandler:
    """Minimal HTTP handler used by the proxy endpoint functions."""

    def __init__(self) -> None:
        self.status = 0
        self.headers: dict[str, str] = {}
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def end_headers(self) -> None:
        pass

    def body(self) -> dict[str, Any]:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.mark.parametrize("entry_type", [[], {}, "decision"])
def test_journal_write_rejects_invalid_entry_type(tmp_path, entry_type) -> None:
    state = CompanionState(
        config=CompanionConfig(journal_dir=tmp_path),
        session_id="active-session",
    )

    result = json.loads(
        mcp_tools._handle_journal_write(
            state,
            {"content": "semantic record", "entry_type": entry_type},
        )
    )

    assert result == {"error": "entry_type must be user, milestone, or handoff"}


def test_semantic_milestone_is_stored_once_with_references_and_recovered(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))

    def endpoint_post(path: str, body: dict[str, Any], params=None):
        del params
        prefix = "/tpk/v1/journal/"
        suffix = "/entry"
        assert path.startswith(prefix) and path.endswith(suffix)
        session_id = unquote(path[len(prefix) : -len(suffix)])
        handler = _EndpointHandler()
        app_endpoints._handle_journal_post(handler, session_id, body)
        return handler.status, handler.body()

    def endpoint_get(path: str, params: dict[str, Any] | None = None):
        prefix = "/tpk/v1/journal/"
        assert path.startswith(prefix)
        session_id = unquote(path[len(prefix) :])
        query = {key: [str(value)] for key, value in (params or {}).items()}
        handler = _EndpointHandler()
        app_endpoints._handle_journal_get(handler, session_id, query)
        return handler.status, handler.body()

    monkeypatch.setattr(mcp_tools, "_proxy_post", endpoint_post)
    monkeypatch.setattr(mcp_tools, "_proxy_get", endpoint_get)
    state = CompanionState(
        config=CompanionConfig(journal_dir=tmp_path),
        session_id="interrupted-session",
    )
    content = (
        "Decision: keep instruction-bearing turns verbatim. "
        "Reason: compression cannot distinguish narrative from constraints. "
        "Changed constraint: do not reduce semantic capture yet. "
        "Blocker: focused request-pipeline checks are incomplete. "
        "Next: resume those checks."
    )
    arguments = {
        "content": content,
        "entry_type": "milestone",
        "references": ["source:companion/capsules/builder.py", "test:instruction-integrity"],
    }

    first = json.loads(mcp_tools._handle_journal_write(state, arguments))
    repeated = json.loads(mcp_tools._handle_journal_write(state, arguments))
    recovered = json.loads(
        mcp_tools._handle_journal_read(
            state,
            {"session_id": "interrupted-session", "entry_type": "milestone"},
        )
    )

    assert first["status"] == repeated["status"] == "ok"
    assert recovered["entries"] == [
        {
            "timestamp": recovered["entries"][0]["timestamp"],
            "type": "milestone",
            "content": content,
            "metadata": {"references": arguments["references"]},
        }
    ]
