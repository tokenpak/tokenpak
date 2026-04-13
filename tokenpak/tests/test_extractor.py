"""Unit tests for extraction/extractor.py — EntityExtractor pipeline."""

import json

import pytest

from extraction.extractor import EntityExtractor
from extraction.models import EntitySet, EntityType


@pytest.fixture
def extractor():
    return EntityExtractor()


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestEntityExtractorInit:
    def test_instantiation_no_args(self):
        e = EntityExtractor()
        assert isinstance(e, EntityExtractor)

    def test_multiple_instances_are_independent(self):
        a = EntityExtractor()
        b = EntityExtractor()
        assert a is not b


# ---------------------------------------------------------------------------
# extract() — empty / trivial inputs
# ---------------------------------------------------------------------------


class TestExtractEmpty:
    def test_empty_string(self, extractor):
        result = extractor.extract("")
        assert isinstance(result, EntitySet)
        assert result.entities == []
        assert result.decisions == []
        assert result.deadlines == []
        assert result.api_endpoints == []
        assert result.glossary_terms == []

    def test_whitespace_only(self, extractor):
        result = extractor.extract("   \n\t  ")
        assert result.entities == []

    def test_plain_prose_no_entities(self, extractor):
        result = extractor.extract("this is just some regular text with no special tokens")
        # No uppercase config keys, no paths, no dates — may or may not match person
        # The key assertion is that it returns an EntitySet without error
        assert isinstance(result, EntitySet)


# ---------------------------------------------------------------------------
# extract() — file paths
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    def test_unix_absolute_path(self, extractor):
        result = extractor.extract("/home/user/config.yaml")
        paths = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert "/home/user/config.yaml" in paths

    def test_home_relative_path(self, extractor):
        result = extractor.extract("Config lives at ~/vault/config.yaml")
        paths = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert any("vault/config.yaml" in p for p in paths)

    def test_short_path_filtered(self, extractor):
        # A path of 2 chars or fewer is filtered in _extract_paths
        result = extractor.extract("path is /x")
        paths = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert "/x" not in paths

    def test_source_ref_populated(self, extractor):
        result = extractor.extract("/etc/hosts")
        path_entities = [e for e in result.entities if e.type == EntityType.FILE_PATH]
        assert len(path_entities) >= 1
        ref = path_entities[0].source
        assert ref.line == 1
        assert ref.snippet != ""


# ---------------------------------------------------------------------------
# extract() — API endpoints
# ---------------------------------------------------------------------------


class TestExtractAPIEndpoints:
    def test_get_endpoint(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        assert len(result.api_endpoints) == 1
        ep = result.api_endpoints[0]
        assert ep.method == "GET"
        assert ep.path == "/api/v1/users"

    def test_post_endpoint(self, extractor):
        result = extractor.extract("POST /api/v1/resources")
        ep = result.api_endpoints[0]
        assert ep.method == "POST"

    def test_path_only_endpoint(self, extractor):
        result = extractor.extract("call /api/v2/items for data")
        eps = result.api_endpoints
        assert any(ep.path == "/api/v2/items" for ep in eps)
        assert any(ep.method is None for ep in eps)

    def test_path_with_zero_slashes_filtered(self, extractor):
        # _extract_api filters path.count("/") < 1, so paths with >= 1 slash pass.
        # No bare path with zero slashes can match API_ENDPOINT_RE anyway (pattern requires
        # leading /), so extracting a non-path word produces no api_endpoints.
        result = extractor.extract("notapath here")
        assert result.api_endpoints == []

    def test_endpoint_also_in_entities(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        api_entities = [e for e in result.entities if e.type == EntityType.API_ENDPOINT]
        assert len(api_entities) >= 1

    def test_source_line_number(self, extractor):
        text = "line one\nGET /api/v2/things"
        result = extractor.extract(text)
        eps = result.api_endpoints
        assert any(ep.source.line == 2 for ep in eps)


# ---------------------------------------------------------------------------
# extract() — dates / deadlines
# ---------------------------------------------------------------------------


class TestExtractDates:
    def test_iso_date(self, extractor):
        result = extractor.extract("Due 2026-04-12")
        assert len(result.deadlines) == 1
        dl = result.deadlines[0]
        assert dl.text == "2026-04-12"
        assert dl.normalized == "2026-04-12"

    def test_us_date(self, extractor):
        result = extractor.extract("Due 04/12/2026")
        assert len(result.deadlines) == 1
        assert result.deadlines[0].normalized == "2026-04-12"

    def test_abbreviated_month(self, extractor):
        result = extractor.extract("Due Apr 12, 2026")
        assert len(result.deadlines) == 1
        assert result.deadlines[0].normalized == "2026-04-12"

    def test_date_also_in_entities(self, extractor):
        result = extractor.extract("Due 2026-04-12")
        deadline_entities = [e for e in result.entities if e.type == EntityType.DEADLINE]
        assert len(deadline_entities) == 1


# ---------------------------------------------------------------------------
# extract() — decisions
# ---------------------------------------------------------------------------


class TestExtractDecisions:
    def test_decision_keyword(self, extractor):
        result = extractor.extract("decision: use postgresql")
        assert len(result.decisions) == 1
        assert "postgresql" in result.decisions[0].text

    def test_we_will_keyword(self, extractor):
        result = extractor.extract("we will deploy on friday")
        assert len(result.decisions) >= 1

    def test_approved_keyword(self, extractor):
        result = extractor.extract("approved the new architecture")
        assert len(result.decisions) >= 1

    def test_decision_also_in_entities(self, extractor):
        result = extractor.extract("decision: use redis")
        decision_entities = [e for e in result.entities if e.type == EntityType.DECISION]
        assert len(decision_entities) >= 1

    def test_no_decision_in_plain_text(self, extractor):
        result = extractor.extract("the cat sat on the mat")
        assert result.decisions == []


# ---------------------------------------------------------------------------
# extract() — glossary terms
# ---------------------------------------------------------------------------


class TestExtractGlossary:
    def test_term_keyword(self, extractor):
        result = extractor.extract("term: TokenPak")
        assert len(result.glossary_terms) == 1
        assert result.glossary_terms[0].term == "TokenPak"

    def test_glossary_keyword(self, extractor):
        result = extractor.extract("glossary: Context Window")
        assert len(result.glossary_terms) == 1

    def test_definition_is_none(self, extractor):
        result = extractor.extract("term: Embedding")
        assert result.glossary_terms[0].definition is None

    def test_glossary_also_in_entities(self, extractor):
        result = extractor.extract("term: TokenPak")
        glossary_entities = [e for e in result.entities if e.type == EntityType.GLOSSARY_TERM]
        assert len(glossary_entities) >= 1


# ---------------------------------------------------------------------------
# extract() — config keys
# ---------------------------------------------------------------------------


class TestExtractConfigKeys:
    def test_all_caps_key(self, extractor):
        result = extractor.extract("Set DATABASE_URL=postgres")
        config_entities = [e for e in result.entities if e.type == EntityType.CONFIG_KEY]
        assert any(e.value == "DATABASE_URL" for e in config_entities)

    def test_multiple_keys(self, extractor):
        result = extractor.extract("LOG_LEVEL and MAX_RETRIES matter")
        config_entities = [e.value for e in result.entities if e.type == EntityType.CONFIG_KEY]
        assert "LOG_LEVEL" in config_entities
        assert "MAX_RETRIES" in config_entities


# ---------------------------------------------------------------------------
# extract() — people and organizations
# ---------------------------------------------------------------------------


class TestExtractPeopleOrgs:
    def test_person_detection(self, extractor):
        result = extractor.extract("Alice Smith approved the change")
        person_entities = [e for e in result.entities if e.type == EntityType.PERSON]
        assert any(e.value == "Alice Smith" for e in person_entities)

    def test_org_detection(self, extractor):
        result = extractor.extract("Funded by Acme Inc")
        org_entities = [e for e in result.entities if e.type == EntityType.ORGANIZATION]
        assert len(org_entities) >= 1


# ---------------------------------------------------------------------------
# extract() — code block stripping
# ---------------------------------------------------------------------------


class TestExtractCodeBlockStripping:
    def test_content_inside_fenced_block_is_ignored(self, extractor):
        text = "```python\n/secret/path/inside/code\n```"
        result = extractor.extract(text)
        paths = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert "/secret/path/inside/code" not in paths

    def test_content_outside_fenced_block_extracted(self, extractor):
        text = "```python\nsome code\n```\n/real/path/here"
        result = extractor.extract(text)
        paths = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert any("/real/path/here" in p for p in paths)

    def test_multiple_code_blocks_stripped(self, extractor):
        text = "```\nGET /hidden/endpoint\n```\ntext\n```\nPOST /also/hidden\n```"
        result = extractor.extract(text)
        ep_paths = [ep.path for ep in result.api_endpoints]
        assert "/hidden/endpoint" not in ep_paths
        assert "/also/hidden" not in ep_paths


# ---------------------------------------------------------------------------
# extract() — deduplication
# ---------------------------------------------------------------------------


class TestExtractDeduplication:
    def test_duplicate_file_paths_deduped(self, extractor):
        text = "/home/user/file.txt\n/home/user/file.txt"
        result = extractor.extract(text)
        path_values = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert path_values.count("/home/user/file.txt") == 1

    def test_duplicate_api_endpoints_deduped(self, extractor):
        text = "GET /api/v1/users\nGET /api/v1/users\nGET /api/v1/users"
        result = extractor.extract(text)
        assert len(result.api_endpoints) == 1

    def test_duplicate_decisions_deduped(self, extractor):
        text = "decision: use redis\ndecision: use redis"
        result = extractor.extract(text)
        assert len(result.decisions) == 1

    def test_duplicate_deadlines_deduped(self, extractor):
        text = "Due 2026-04-12\nDue 2026-04-12"
        result = extractor.extract(text)
        assert len(result.deadlines) == 1

    def test_duplicate_glossary_terms_deduped(self, extractor):
        text = "term: TokenPak\nterm: TokenPak"
        result = extractor.extract(text)
        assert len(result.glossary_terms) == 1

    def test_case_insensitive_entity_dedup(self, extractor):
        # Same value, different case should be treated as duplicate
        text = "/Home/User/File.txt\n/home/user/file.txt"
        result = extractor.extract(text)
        path_values = [e.value for e in result.entities if e.type == EntityType.FILE_PATH]
        assert len(path_values) == 1


# ---------------------------------------------------------------------------
# extract() — multiline / source tracking
# ---------------------------------------------------------------------------


class TestExtractSourceTracking:
    def test_line_numbers_are_correct(self, extractor):
        text = "line 1\nline 2\nGET /api/v1/test"
        result = extractor.extract(text)
        eps = result.api_endpoints
        assert any(ep.source.line == 3 for ep in eps)

    def test_snippet_truncated_to_240(self, extractor):
        long_line = "/home/user/file.txt " + "x" * 300
        result = extractor.extract(long_line)
        for e in result.entities:
            assert len(e.source.snippet) <= 240


# ---------------------------------------------------------------------------
# extract() — large input
# ---------------------------------------------------------------------------


class TestExtractLargeInput:
    def test_large_repeated_content_deduped(self, extractor):
        text = "GET /api/v1/users\n" * 1000
        result = extractor.extract(text)
        assert len(result.api_endpoints) == 1

    def test_large_varied_content(self, extractor):
        lines = [f"GET /api/v1/resource/{i}" for i in range(100)]
        text = "\n".join(lines)
        result = extractor.extract(text)
        assert len(result.api_endpoints) == 100


# ---------------------------------------------------------------------------
# extract() — unicode
# ---------------------------------------------------------------------------


class TestExtractUnicode:
    def test_unicode_does_not_crash(self, extractor):
        result = extractor.extract("Héllo wörld résumé")
        assert isinstance(result, EntitySet)

    def test_path_with_unicode_truncated_at_boundary(self, extractor):
        # Unicode in path: regex stops at non-ascii character
        result = extractor.extract("/api/v1/üsers")
        # Should not crash; path may be partial or absent
        assert isinstance(result, EntitySet)


# ---------------------------------------------------------------------------
# compact_text()
# ---------------------------------------------------------------------------


class TestCompactText:
    def test_returns_valid_json(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        compact = extractor.compact_text(result)
        parsed = json.loads(compact)
        assert isinstance(parsed, dict)

    def test_compact_dict_has_all_keys(self, extractor):
        result = extractor.extract("")
        compact = extractor.compact_text(result)
        parsed = json.loads(compact)
        expected_keys = {
            "people", "organizations", "config_keys", "file_paths",
            "api_endpoints", "decisions", "deadlines", "glossary",
        }
        assert expected_keys == set(parsed.keys())

    def test_compact_text_sorted_keys(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        compact = extractor.compact_text(result)
        # sort_keys=True means keys appear in alphabetical order
        keys = list(json.loads(compact).keys())
        assert keys == sorted(keys)

    def test_compact_text_uses_compact_separators(self, extractor):
        result = extractor.extract("")
        compact = extractor.compact_text(result)
        # separators=(",",":") means no ": " or ", " in the JSON structure
        assert ": " not in compact
        assert ", " not in compact


# ---------------------------------------------------------------------------
# choose_injection()
# ---------------------------------------------------------------------------


class TestChooseInjection:
    def test_prefer_compact_true_returns_compact(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        output = EntityExtractor.choose_injection("raw text", result, prefer_compact=True)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_prefer_compact_false_returns_raw(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        output = EntityExtractor.choose_injection("raw text", result, prefer_compact=False)
        assert output == "raw text"

    def test_default_is_compact(self, extractor):
        result = extractor.extract("GET /api/v1/users")
        output = EntityExtractor.choose_injection("raw text", result)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# _strip_code_blocks() (static method)
# ---------------------------------------------------------------------------


class TestStripCodeBlocks:
    def test_removes_fenced_block(self):
        text = "before\n```python\nsome code\n```\nafter"
        result = EntityExtractor._strip_code_blocks(text)
        assert "some code" not in result
        assert "before" in result
        assert "after" in result

    def test_noop_on_plain_text(self):
        text = "no code blocks here"
        assert EntityExtractor._strip_code_blocks(text) == text

    def test_multiple_blocks_removed(self):
        text = "```a\nfoo\n```\ntxt\n```b\nbar\n```"
        result = EntityExtractor._strip_code_blocks(text)
        assert "foo" not in result
        assert "bar" not in result
        assert "txt" in result


# ---------------------------------------------------------------------------
# _normalize_date() (static method)
# ---------------------------------------------------------------------------


class TestNormalizeDate:
    def test_iso_format(self):
        assert EntityExtractor._normalize_date("2026-04-12") == "2026-04-12"

    def test_us_format(self):
        assert EntityExtractor._normalize_date("04/12/2026") == "2026-04-12"

    def test_abbreviated_month(self):
        assert EntityExtractor._normalize_date("Apr 12, 2026") == "2026-04-12"

    def test_full_month(self):
        assert EntityExtractor._normalize_date("April 12, 2026") == "2026-04-12"

    def test_invalid_returns_none(self):
        assert EntityExtractor._normalize_date("not-a-date") is None

    def test_partial_date_returns_none(self):
        assert EntityExtractor._normalize_date("2026-04") is None
