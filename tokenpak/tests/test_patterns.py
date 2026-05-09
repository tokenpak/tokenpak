"""Unit tests for extraction/patterns.py — regex pattern compilation and matching."""

import re

from extraction.patterns import (
    API_ENDPOINT_RE,
    CONFIG_KEY_RE,
    DATE_RE,
    DECISION_RE,
    FENCED_CODE_RE,
    FILE_PATH_RE,
    GLOSSARY_RE,
    ORG_RE,
    PERSON_RE,
)

# ---------------------------------------------------------------------------
# FILE_PATH_RE
# ---------------------------------------------------------------------------


class TestFilePathRE:
    def test_unix_absolute_path(self):
        m = FILE_PATH_RE.search("/home/user/file.txt")
        assert m is not None
        assert m.group("path") == "/home/user/file.txt"

    def test_home_relative_path(self):
        m = FILE_PATH_RE.search("~/vault/data.md")
        assert m is not None
        assert m.group("path").startswith("~/")

    def test_windows_path(self):
        m = FILE_PATH_RE.search(r"C:\Users\alice\docs\report.txt")
        assert m is not None

    def test_no_match_on_plain_word(self):
        assert FILE_PATH_RE.search("hello world") is None

    def test_multiple_paths(self):
        text = "/etc/hosts and /tmp/foo.log"
        matches = [m.group("path") for m in FILE_PATH_RE.finditer(text)]
        assert "/etc/hosts" in matches
        assert "/tmp/foo.log" in matches


# ---------------------------------------------------------------------------
# API_ENDPOINT_RE
# ---------------------------------------------------------------------------


class TestAPIEndpointRE:
    def test_get_with_path(self):
        m = API_ENDPOINT_RE.search("GET /api/v1/users")
        assert m is not None
        assert m.group("method") == "GET"
        assert m.group("path") == "/api/v1/users"

    def test_path_only_no_method(self):
        m = API_ENDPOINT_RE.search("/api/v2/items")
        assert m is not None
        assert m.group("method") is None
        assert m.group("path") == "/api/v2/items"

    def test_all_http_methods(self):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            m = API_ENDPOINT_RE.search(f"{method} /endpoint/path")
            assert m is not None, f"Expected match for {method}"
            assert m.group("method") == method

    def test_path_with_template_params(self):
        m = API_ENDPOINT_RE.search("GET /api/v1/users/{id}")
        assert m is not None
        assert "{id}" in m.group("path")

    def test_no_match_on_plain_word(self):
        # A single-segment path like "/foo" has count("/") == 1, which extractor filters,
        # but the pattern itself still matches — test raw RE behavior
        m = API_ENDPOINT_RE.search("not a path")
        assert m is None


# ---------------------------------------------------------------------------
# DATE_RE
# ---------------------------------------------------------------------------


class TestDateRE:
    def test_iso_format(self):
        m = DATE_RE.search("Due 2026-04-12")
        assert m is not None
        assert m.group(0) == "2026-04-12"

    def test_us_format(self):
        m = DATE_RE.search("Due 04/12/2026")
        assert m is not None
        assert m.group(0) == "04/12/2026"

    def test_long_month_format(self):
        m = DATE_RE.search("April 12, 2026")
        assert m is not None

    def test_abbreviated_month_format(self):
        m = DATE_RE.search("Apr 12, 2026")
        assert m is not None
        assert m.group(0) == "Apr 12, 2026"

    def test_case_insensitive_month(self):
        m = DATE_RE.search("jan 01, 2026")
        assert m is not None

    def test_no_match_on_invalid_date(self):
        assert DATE_RE.search("1999-01-01") is None  # year outside 20xx range

    def test_multiple_dates_in_text(self):
        text = "Start 2026-01-01 and end 2026-12-31"
        matches = [m.group(0) for m in DATE_RE.finditer(text)]
        assert "2026-01-01" in matches
        assert "2026-12-31" in matches


# ---------------------------------------------------------------------------
# DECISION_RE
# ---------------------------------------------------------------------------


class TestDecisionRE:
    def test_decision_keyword(self):
        m = DECISION_RE.search("decision: use postgres")
        assert m is not None
        assert "postgres" in m.group("text")

    def test_decided_keyword(self):
        m = DECISION_RE.search("We decided to roll back")
        assert m is not None

    def test_we_will_keyword(self):
        m = DECISION_RE.search("we will deploy on Friday")
        assert m is not None
        assert "deploy on Friday" in m.group("text")

    def test_approved_keyword(self):
        m = DECISION_RE.search("approved by leadership")
        assert m is not None

    def test_rejected_keyword(self):
        m = DECISION_RE.search("rejected the proposal")
        assert m is not None

    def test_no_match_on_plain_text(self):
        assert DECISION_RE.search("hello world this is plain") is None

    def test_case_insensitive(self):
        m = DECISION_RE.search("DECISION: use redis")
        assert m is not None


# ---------------------------------------------------------------------------
# GLOSSARY_RE
# ---------------------------------------------------------------------------


class TestGlossaryRE:
    def test_term_keyword(self):
        m = GLOSSARY_RE.search("term: TokenPak")
        assert m is not None
        assert m.group("term").strip() == "TokenPak"

    def test_glossary_keyword(self):
        m = GLOSSARY_RE.search("glossary: Context Window")
        assert m is not None
        assert "Context Window" in m.group("term")

    def test_case_insensitive(self):
        m = GLOSSARY_RE.search("TERM: Embedding")
        assert m is not None

    def test_no_match_on_plain_text(self):
        assert GLOSSARY_RE.search("some plain sentence here") is None


# ---------------------------------------------------------------------------
# CONFIG_KEY_RE
# ---------------------------------------------------------------------------


class TestConfigKeyRE:
    def test_all_caps_key(self):
        m = CONFIG_KEY_RE.search("DATABASE_URL=postgres")
        assert m is not None
        assert m.group(1) == "DATABASE_URL"

    def test_key_with_digits(self):
        m = CONFIG_KEY_RE.search("API_KEY_V2=abc")
        assert m is not None
        assert m.group(1) == "API_KEY_V2"

    def test_minimum_length(self):
        # Must be at least 3 chars total (1 uppercase + 2 more)
        assert CONFIG_KEY_RE.search("AB") is None

    def test_lowercase_not_matched(self):
        # Pattern requires leading uppercase; "hello" won't match
        matches = CONFIG_KEY_RE.findall("hello world")
        assert matches == []

    def test_multiple_keys(self):
        text = "Set LOG_LEVEL and MAX_RETRIES"
        matches = CONFIG_KEY_RE.findall(text)
        assert "LOG_LEVEL" in matches
        assert "MAX_RETRIES" in matches


# ---------------------------------------------------------------------------
# PERSON_RE
# ---------------------------------------------------------------------------


class TestPersonRE:
    def test_two_capitalized_words(self):
        m = PERSON_RE.search("Alice Smith approved")
        assert m is not None
        assert m.group(1) == "Alice Smith"

    def test_no_match_single_name(self):
        assert PERSON_RE.search("Alice approved") is None

    def test_no_match_all_caps(self):
        assert PERSON_RE.search("ALICE SMITH") is None

    def test_multiple_people(self):
        text = "John Doe and Jane Doe attended"
        matches = [m.group(1) for m in PERSON_RE.finditer(text)]
        assert "John Doe" in matches
        assert "Jane Doe" in matches


# ---------------------------------------------------------------------------
# ORG_RE
# ---------------------------------------------------------------------------


class TestOrgRE:
    def test_inc_suffix(self):
        m = ORG_RE.search("Acme Inc")
        assert m is not None

    def test_corp_suffix(self):
        m = ORG_RE.search("MegaCorp Corp")
        assert m is not None

    def test_systems_suffix(self):
        m = ORG_RE.search("Quantum Systems approved")
        assert m is not None

    def test_no_match_lowercase(self):
        assert ORG_RE.search("acme inc") is None

    def test_no_match_no_suffix(self):
        assert ORG_RE.search("RandomCompany") is None


# ---------------------------------------------------------------------------
# FENCED_CODE_RE
# ---------------------------------------------------------------------------


class TestFencedCodeRE:
    def test_matches_fenced_block(self):
        text = "```python\nprint('hello')\n```"
        m = FENCED_CODE_RE.search(text)
        assert m is not None

    def test_sub_removes_block(self):
        text = "Before\n```python\ncode\n```\nAfter"
        result = FENCED_CODE_RE.sub("", text)
        assert "code" not in result
        assert "Before" in result
        assert "After" in result

    def test_multiple_blocks_removed(self):
        text = "```a\nfoo\n```\ntext\n```b\nbar\n```"
        result = FENCED_CODE_RE.sub("", text)
        assert "foo" not in result
        assert "bar" not in result
        assert "text" in result

    def test_no_match_on_plain_text(self):
        assert FENCED_CODE_RE.search("no code blocks here") is None

    def test_dotall_flag_set(self):
        assert FENCED_CODE_RE.flags & re.DOTALL
