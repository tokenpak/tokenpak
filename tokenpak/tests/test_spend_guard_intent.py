# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.proxy.spend_guard.intent — TSG-03 acceptance."""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.spend_guard.intent import Intent, parse_intent


def _body(text: str) -> bytes:
    return json.dumps({
        "model": "claude-opus-4-7",
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": text}],
    }).encode()


class TestPositive:
    @pytest.mark.parametrize("word", [
        "yes", "Yes", "YES", "y",
        "ok", "OK", "okay",
        "go", "go ahead", "Go Ahead",
        "proceed", "continue", "approved",
        "sure", "fine", "alright",
        "do it", "run it", "ship it",
        "yep", "yeah", "yup",
        "let's go", "lets go",
        "confirm", "confirmed",
    ])
    def test_positive_variants(self, word):
        assert parse_intent(_body(word)) == Intent.POSITIVE

    def test_with_trailing_punctuation(self):
        assert parse_intent(_body("yes!")) == Intent.POSITIVE
        assert parse_intent(_body("go.")) == Intent.POSITIVE
        assert parse_intent(_body("sure!!")) == Intent.POSITIVE

    def test_with_whitespace(self):
        assert parse_intent(_body("  yes  ")) == Intent.POSITIVE
        assert parse_intent(_body("\nproceed\n")) == Intent.POSITIVE


class TestNegative:
    @pytest.mark.parametrize("word", [
        "no", "No", "NO", "n",
        "nope", "nah",
        "stop", "halt", "kill",
        "cancel", "abort",
        "deny", "rejected",
        "don't", "dont", "do not",
        "nevermind", "never mind",
        "skip",
    ])
    def test_negative_variants(self, word):
        assert parse_intent(_body(word)) == Intent.NEGATIVE

    def test_with_punctuation(self):
        assert parse_intent(_body("no!")) == Intent.NEGATIVE
        assert parse_intent(_body("stop.")) == Intent.NEGATIVE


class TestAmbiguous:
    def test_agent_text_with_yes_inside_is_ambiguous(self):
        # Critical: the agent saying "I'll go ahead and write..." must NOT
        # auto-approve the spend guard.
        assert parse_intent(_body("I'll go ahead and write the code")) == Intent.AMBIGUOUS

    def test_full_sentence_is_ambiguous(self):
        assert parse_intent(_body("yes please proceed with caution")) == Intent.AMBIGUOUS

    def test_empty_is_ambiguous(self):
        assert parse_intent(_body("")) == Intent.AMBIGUOUS

    def test_unrelated_text_is_ambiguous(self):
        assert parse_intent(_body("what is the weather?")) == Intent.AMBIGUOUS


class TestLastUserText:
    def test_last_user_message_used(self):
        body = json.dumps({
            "model": "claude-opus-4-7",
            "max_tokens": 1000,
            "messages": [
                {"role": "user", "content": "first message about spending"},
                {"role": "assistant", "content": "I'll proceed with caution"},
                {"role": "user", "content": "yes"},
            ],
        }).encode()
        assert parse_intent(body) == Intent.POSITIVE

    def test_assistant_message_ignored(self):
        body = json.dumps({
            "model": "claude-opus-4-7",
            "max_tokens": 1000,
            "messages": [
                {"role": "user", "content": "what should I do?"},
                {"role": "assistant", "content": "yes"},
            ],
        }).encode()
        # No user message after the assistant — last user message is "what should I do?"
        assert parse_intent(body) == Intent.AMBIGUOUS

    def test_content_block_array(self):
        body = json.dumps({
            "model": "claude-opus-4-7",
            "max_tokens": 1000,
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "yes"}]},
            ],
        }).encode()
        assert parse_intent(body) == Intent.POSITIVE


class TestMalformed:
    def test_non_json_falls_back(self):
        # Plain text "yes" with no JSON structure should still parse.
        assert parse_intent(b"yes") == Intent.POSITIVE
        assert parse_intent(b"no") == Intent.NEGATIVE

    def test_empty_bytes(self):
        assert parse_intent(b"") == Intent.AMBIGUOUS
