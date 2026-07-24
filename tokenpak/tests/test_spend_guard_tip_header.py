# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.proxy.spend_guard.tip_header."""

from __future__ import annotations

import json

from tokenpak.proxy.spend_guard.tip_header import (
    DIRECTIVE_REGISTRY,
    parse_and_strip_tip_header,
    parse_tip_header,
    strip_tip_header,
)


class TestRegistry:
    def test_v1_functions_present(self):
        # Single source of truth — adding a directive elsewhere must fail
        # gracefully via unknown_keys, not pass silently.
        for name in ("allow", "bypass", "max", "estimate", "cancel", "reason"):
            assert name in DIRECTIVE_REGISTRY


class TestParseString:
    def test_allow_once(self):
        d, rest = parse_tip_header("[TIP: allow=once] do the thing")
        assert d is not None
        assert d.allow_scope == "once"
        assert rest == "do the thing"

    def test_allow_15m(self):
        d, _ = parse_tip_header("[TIP: allow=15m]")
        assert d.allow_scope == "15m"

    def test_allow_session(self):
        d, _ = parse_tip_header("[TIP: allow=session]")
        assert d.allow_scope == "session"

    def test_max_dollar(self):
        d, _ = parse_tip_header("[TIP: allow=once max=$10]")
        assert d.allow_scope == "once"
        assert d.max_cost_usd == 10.0

    def test_max_dollar_decimal(self):
        d, _ = parse_tip_header("[TIP: max=$1.50]")
        assert d.max_cost_usd == 1.5

    def test_max_k_tokens(self):
        d, _ = parse_tip_header("[TIP: max=500k_tokens]")
        assert d.max_tokens == 500_000

    def test_max_m_tokens(self):
        d, _ = parse_tip_header("[TIP: max=2m_tokens]")
        assert d.max_tokens == 2_000_000

    def test_bypass(self):
        d, _ = parse_tip_header("[TIP: bypass=on]")
        assert d.bypass is True

    def test_bypass_bare(self):
        d, _ = parse_tip_header("[TIP: bypass]")
        assert d.bypass is True

    def test_estimate(self):
        d, _ = parse_tip_header("[TIP: estimate=on]")
        assert d.estimate_only is True

    def test_cancel(self):
        d, _ = parse_tip_header("[TIP: cancel]")
        assert d.cancel is True

    def test_reason_quoted(self):
        d, _ = parse_tip_header('[TIP: allow=once reason="planned refactor"]')
        assert d.reason == "planned refactor"
        assert d.allow_scope == "once"

    def test_combined_directives(self):
        d, rest = parse_tip_header(
            '[TIP: allow=once max=$15 reason="deep refactor"] now refactor X'
        )
        assert d.allow_scope == "once"
        assert d.max_cost_usd == 15.0
        assert d.reason == "deep refactor"
        assert rest == "now refactor X"

    def test_leading_whitespace_ok(self):
        d, _ = parse_tip_header("   [TIP: allow=once]   payload")
        assert d.allow_scope == "once"

    def test_unknown_key_collected(self):
        d, _ = parse_tip_header("[TIP: foo=bar allow=once]")
        assert "foo" in d.unknown_keys
        assert d.allow_scope == "once"  # known still applied


class TestNoMatch:
    def test_mid_sentence_not_parsed(self):
        # The proposal §grammar: header MUST be at the very front.
        d, rest = parse_tip_header("hello [TIP: allow=once] world")
        assert d is None
        assert rest == "hello [TIP: allow=once] world"

    def test_empty_input(self):
        d, rest = parse_tip_header("")
        assert d is None
        assert rest == ""

    def test_no_brackets(self):
        d, rest = parse_tip_header("TIP allow=once")
        assert d is None


class TestStripFromBody:
    def _body(self, content) -> bytes:
        return json.dumps(
            {
                "model": "claude-opus-4-7",
                "max_tokens": 1000,
                "messages": [{"role": "user", "content": content}],
            }
        ).encode()

    def test_strip_from_string_content(self):
        body = self._body("[TIP: allow=once] do the thing")
        d, modified = parse_and_strip_tip_header(body)
        assert d is not None
        assert d.allow_scope == "once"
        # Verify the [TIP:...] marker is gone from forwarded body.
        modified_json = json.loads(modified.decode("utf-8"))
        assert "[TIP:" not in modified_json["messages"][0]["content"]
        assert "do the thing" in modified_json["messages"][0]["content"]

    def test_strip_from_block_content(self):
        body = json.dumps(
            {
                "model": "claude-opus-4-7",
                "max_tokens": 1000,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "[TIP: bypass=on] proceed"},
                        ],
                    },
                ],
            }
        ).encode()
        d, modified = parse_and_strip_tip_header(body)
        assert d is not None
        assert d.bypass is True
        modified_json = json.loads(modified.decode("utf-8"))
        assert "[TIP:" not in modified_json["messages"][0]["content"][0]["text"]

    def test_no_directive_returns_original_body_object(self):
        body = self._body("just normal text")
        d, modified = parse_and_strip_tip_header(body)
        assert d is None
        # Zero-cost path: returns same object.
        assert modified is body

    def test_empty_body(self):
        d, modified = parse_and_strip_tip_header(b"")
        assert d is None
        assert modified == b""


class TestStripConvenience:
    def test_strip_tip_header_string(self):
        s = strip_tip_header("[TIP: allow=once] hello")
        assert s == "hello"

    def test_strip_no_op_when_absent(self):
        s = strip_tip_header("hello world")
        assert s == "hello world"
