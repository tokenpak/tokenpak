"""Deterministic end-to-end proof for the eligible first-receipt path."""

from __future__ import annotations

import json
import sqlite3

import pytest

from tests.proxy._proxy_subprocess import REPO_ROOT, ProxyProc

pytestmark = [pytest.mark.needs_proxy, pytest.mark.timeout(120)]


@pytest.mark.parametrize(
    ("mode", "content", "extra_headers"),
    [
        ("transparent", "A transparent request must remain unchanged.", None),
        ("strict", "A strict request must remain unchanged.", None),
        ("safe", "A safe request must remain unchanged.", None),
        ("aggressive", "short", None),
        (
            "aggressive",
            "A Claude Code request is byte-preserved regardless of mode.",
            {"X-Claude-Code-Session-Id": "receipt-honesty-test"},
        ),
    ],
)
def test_ineligible_request_is_byte_preserved_and_never_claims_positive_savings(
    stub_upstream,
    mode: str,
    content: str,
    extra_headers: dict[str, str] | None,
) -> None:
    """Zero-reduction paths report zero and preserve provider request bytes."""
    proxy = ProxyProc(
        f"http://127.0.0.1:{stub_upstream.server_port}",
        extra_env={
            "TOKENPAK_MODE": mode,
            "TOKENPAK_STATS_FOOTER": "1",
        },
    )
    messages = [{"role": "user", "content": content}]
    expected_body = json.dumps(
        {
            "model": "claude-sonnet-4-5",
            "max_tokens": 32,
            "messages": messages,
        }
    ).encode()
    try:
        proxy.wait_ready()
        status, _, body = proxy.post_messages(messages, extra_headers=extra_headers)

        assert status == 200
        assert b"msg_" in body
        assert stub_upstream.last_request_body == expected_body
        assert proxy.wait_row_count(1) == 1

        conn = sqlite3.connect(f"file:{proxy.db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                "SELECT compressed_tokens, would_have_saved FROM requests"
            ).fetchone()
        finally:
            conn.close()

        assert row == (0, 0)
        stderr = proxy.stderr()
        assert "⚡ TokenPak: 0 tokens saved" in stderr
        assert "⚡ TokenPak: -" not in stderr
    finally:
        proxy.cleanup()


def test_eligible_first_request_produces_positive_measured_receipt(
    stub_upstream,
) -> None:
    """The documented request traverses proxy, upstream, ledger, and footer."""
    proxy = ProxyProc(
        f"http://127.0.0.1:{stub_upstream.server_port}",
        extra_env={
            "TOKENPAK_PROFILE": "aggressive",
            "TOKENPAK_STATS_FOOTER": "1",
        },
    )
    try:
        proxy.wait_ready()
        project_context = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        # Keep the fixture eligible even when normal README editing moves it
        # just below the request-size threshold. Repetition remains real project
        # context and gives the deterministic compressor a measurable target.
        if len(project_context) < 8_000:
            project_context = f"{project_context}\n\n{project_context}"
        assert len(project_context) >= 8_000

        status, _, body = proxy.post_messages(
            [
                {
                    "role": "user",
                    "content": f"Project document README.md:\n\n{project_context}",
                },
                {"role": "assistant", "content": "Project context received."},
                {
                    "role": "user",
                    "content": (
                        "Review this project context and identify five concrete "
                        "release-readiness risks, citing the README.md section for each."
                    ),
                },
            ]
        )

        assert status == 200
        assert b"msg_" in body
        assert stub_upstream.request_count == 1
        assert proxy.wait_row_count(1) == 1

        conn = sqlite3.connect(f"file:{proxy.db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute(
                """
                SELECT input_tokens, compressed_tokens, would_have_saved, status_code
                FROM requests
                """
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        raw_tokens, compressed_tokens, would_have_saved, status_code = row
        sent_tokens = raw_tokens - compressed_tokens
        assert status_code == 200
        assert raw_tokens > sent_tokens > 0
        assert compressed_tokens == would_have_saved
        assert compressed_tokens > 0

        stderr = proxy.stderr()
        assert "⚡ TokenPak:" in stderr
        assert f"-{compressed_tokens:,} tokens" in stderr
        assert "saved" in stderr
    finally:
        proxy.cleanup()
