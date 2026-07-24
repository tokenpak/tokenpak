"""Traffic-classifier contract tests.

Fixture policy: agent-name fixture values in this module are neutral
placeholder identifiers (``agent-a``, ``worker-1``, ...) and must match
``_NEUTRAL_IDENTIFIER`` below — never real deployment or operator names.
A regression guard at the bottom of this module enforces the pattern.
"""

import re
from pathlib import Path

from tokenpak.proxy.spend_guard import classifier as c


def test_precedence_and_classes():
    assert c.classify({"X-Tokenpak-Agent": "agent-a"}) == c.Classification(
        c.MANAGED, c.HEADER_AGENT, "agent-a"
    )
    marked = c.classify({"X-Tokenpak-Managed": "1"})
    assert marked.request_class == c.MANAGED
    assert marked.reason == c.HEADER_MANAGED
    assert c.classify({"User-Agent": "claude-cli/1"}).request_class == c.RAW_CLAUDE_OBSERVED
    assert c.classify({"User-Agent": "curl"}).request_class == c.EXTERNAL_UNTAGGED


def test_managed_marker_grammar_is_exact():
    # Only the exact documented marker value counts; alternate truthy-looking
    # tokens do not mark a request managed.
    for value in ("true", "yes", "on", "TRUE", "On", "0", "false", "", "2", "11"):
        result = c.classify({"X-Tokenpak-Managed": value})
        assert result.request_class == c.EXTERNAL_UNTAGGED, value
    # Surrounding whitespace is tolerated (header values are stripped).
    assert c.classify({"X-Tokenpak-Managed": " 1 "}).request_class == c.MANAGED


def test_undocumented_env_marker_header_is_not_consumed():
    # No alternate marker header exists in the ratified grammar; a request
    # carrying only this header is unmarked traffic.
    assert c.classify({"X-Tokenpak-Managed-Env": "1"}).request_class == c.EXTERNAL_UNTAGGED
    assert c.classify({"X-Tokenpak-Managed-Env": "1"}).reason == c.NO_MARKER


def test_higher_precedence_wins_and_false_marker_is_external():
    result = c.classify(
        {"X-Tokenpak-Agent": "worker-1", "X-Tokenpak-Managed": "1", "User-Agent": "claude-cli"}
    )
    assert result.reason == c.HEADER_AGENT
    assert result.agent_attribution == "worker-1"
    assert c.classify({"X-Tokenpak-Managed": "0"}).request_class == c.EXTERNAL_UNTAGGED


def test_case_insensitive_and_read_only():
    headers = {"x-tokenpak-agent": "Agent-B", "User-Agent": "claude-cli"}
    before = dict(headers)
    assert c.classify(headers).agent_attribution == "agent-b"
    assert headers == before


def test_strip_internal_headers_only():
    headers = {
        "X-Tokenpak-Agent": "agent-a",
        "x-tokenpak-managed": "1",
        "X-Tpk-Trace-Id": "trace-1",
        "Authorization": "x",
    }
    removed = c.strip_managed_headers(headers)
    assert headers == {"Authorization": "x"}
    assert set(removed) == {"X-Tokenpak-Agent", "x-tokenpak-managed", "X-Tpk-Trace-Id"}


def test_internal_namespace_predicate():
    assert c.is_internal_header("X-Tokenpak-Managed")
    assert c.is_internal_header("x-tokenpak-agent")
    assert c.is_internal_header("X-TPK-ANYTHING")
    assert not c.is_internal_header("Authorization")
    assert not c.is_internal_header("anthropic-version")
    assert not c.is_internal_header("x-token")  # prefix must match exactly


def test_empty_headers_and_noop_strip():
    assert c.classify(None).reason == c.NO_MARKER
    headers = {"Authorization": "x"}
    assert c.strip_managed_headers(headers) == []


# ---------------------------------------------------------------------------
# Fixture-identifier regression guard
# ---------------------------------------------------------------------------

_NEUTRAL_IDENTIFIER = re.compile(r"^(agent|worker|client)-[a-z0-9]+$", re.IGNORECASE)

_AGENT_FIXTURE_VALUE = re.compile(r"""[Xx]-[Tt]okenpak-[Aa]gent["']\s*:\s*["']([^"']+)["']""")


def test_agent_fixture_identifiers_are_neutral():
    """Agent-name fixture values in this module must be neutral placeholders.

    Guards against reintroducing non-neutral identifiers into the
    ``X-Tokenpak-Agent`` fixtures used by this test module.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    values = _AGENT_FIXTURE_VALUE.findall(source)
    assert values, "expected X-Tokenpak-Agent fixtures in this module"
    for value in values:
        assert _NEUTRAL_IDENTIFIER.match(value), (
            f"non-neutral agent fixture identifier: {value!r} — use a neutral "
            "placeholder matching ^(agent|worker|client)-[a-z0-9]+$"
        )
