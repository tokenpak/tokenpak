"""Tests for manual model routing rules (tokenpak route)."""

import pytest

from tokenpak.routing.rules import (
    RouteEngine,
    RoutePattern,
    RouteRule,
    RouteStore,
    _count_tokens_approx,
    _extract_prompt_text,
    parse_pattern_args,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_routes(tmp_path):
    """Return a RouteStore backed by a temporary file."""
    return RouteStore(path=str(tmp_path / "routes.yaml"))


@pytest.fixture
def engine_with_store(tmp_routes):
    """Return a RouteEngine wired to the temp store."""
    return RouteEngine(store=tmp_routes), tmp_routes


# ---------------------------------------------------------------------------
# RoutePattern unit tests
# ---------------------------------------------------------------------------

class TestRoutePattern:
    def test_is_empty_when_all_none(self):
        pat = RoutePattern()
        assert pat.is_empty()

    def test_not_empty_with_model(self):
        pat = RoutePattern(model="gpt-4*")
        assert not pat.is_empty()

    def test_to_dict_excludes_none(self):
        pat = RoutePattern(model="gpt-4*", min_tokens=500)
        d = pat.to_dict()
        assert "model" in d
        assert "min_tokens" in d
        assert "prefix" not in d
        assert "max_tokens" not in d

    def test_roundtrip(self):
        pat = RoutePattern(model="claude-*", prefix="Translate", min_tokens=100, max_tokens=5000)
        d = pat.to_dict()
        pat2 = RoutePattern.from_dict(d)
        assert pat2.model == "claude-*"
        assert pat2.prefix == "Translate"
        assert pat2.min_tokens == 100
        assert pat2.max_tokens == 5000


# ---------------------------------------------------------------------------
# RouteEngine matching tests
# ---------------------------------------------------------------------------

class TestRouteEngine:
    def _rule(self, pattern, target="haiku", priority=100):
        return RouteRule(
            id="test1",
            pattern=pattern,
            target=target,
            priority=priority,
        )

    def test_model_glob_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(model="gpt-4*"), target="anthropic/claude-3-haiku-20240307")
        result = engine.match(model="gpt-4o", prompt="hello", rules=[rule])
        assert result is not None
        assert result.target == "anthropic/claude-3-haiku-20240307"

    def test_model_glob_no_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(model="gpt-4*"))
        result = engine.match(model="claude-3-opus", prompt="hello", rules=[rule])
        assert result is None

    def test_model_full_provider_path_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(model="openai/*"))
        result = engine.match(model="openai/gpt-4o", prompt="hello", rules=[rule])
        assert result is not None

    def test_prefix_match_case_insensitive(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(prefix="Translate"))
        result = engine.match(model="gpt-4o", prompt="translate this text", rules=[rule])
        assert result is not None

    def test_prefix_no_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(prefix="Summarize"))
        result = engine.match(model="gpt-4o", prompt="translate this text", rules=[rule])
        assert result is None

    def test_min_tokens_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(min_tokens=10))
        result = engine.match(model="gpt-4o", prompt="", token_count=50, rules=[rule])
        assert result is not None

    def test_min_tokens_no_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(min_tokens=100))
        result = engine.match(model="gpt-4o", prompt="hi", token_count=5, rules=[rule])
        assert result is None

    def test_max_tokens_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(max_tokens=1000))
        result = engine.match(model="gpt-4o", prompt="hello", token_count=200, rules=[rule])
        assert result is not None

    def test_max_tokens_no_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(max_tokens=100))
        result = engine.match(model="gpt-4o", prompt="hello", token_count=500, rules=[rule])
        assert result is None

    def test_combined_pattern_all_must_match(self):
        engine = RouteEngine()
        rule = self._rule(RoutePattern(model="gpt-4*", min_tokens=50))
        # model matches, but tokens too low
        result = engine.match(model="gpt-4o", prompt="hi", token_count=10, rules=[rule])
        assert result is None
        # both match
        result = engine.match(model="gpt-4o", prompt="hi", token_count=100, rules=[rule])
        assert result is not None

    def test_priority_ordering_first_wins(self):
        engine = RouteEngine()
        rule_low = RouteRule(id="a", pattern=RoutePattern(model="gpt-*"), target="cheap", priority=200)
        rule_high = RouteRule(id="b", pattern=RoutePattern(model="gpt-*"), target="expensive", priority=10)
        result = engine.match(model="gpt-4o", prompt="hello", rules=[rule_low, rule_high])
        assert result.target == "expensive"  # lower priority number = higher priority

    def test_disabled_rule_skipped(self):
        engine = RouteEngine()
        rule = RouteRule(id="x", pattern=RoutePattern(model="gpt-*"), target="haiku", enabled=False)
        result = engine.match(model="gpt-4o", prompt="hello", rules=[rule])
        assert result is None

    def test_empty_rules_returns_none(self):
        engine = RouteEngine()
        result = engine.match(model="gpt-4o", prompt="hello", rules=[])
        assert result is None


# ---------------------------------------------------------------------------
# RouteStore persistence tests
# ---------------------------------------------------------------------------

class TestRouteStore:
    def test_empty_store(self, tmp_routes):
        assert tmp_routes.list() == []

    def test_add_and_list(self, tmp_routes):
        rule = tmp_routes.add(
            pattern=RoutePattern(model="gpt-4*"),
            target="anthropic/claude-3-haiku-20240307",
        )
        rules = tmp_routes.list()
        assert len(rules) == 1
        assert rules[0].id == rule.id
        assert rules[0].target == "anthropic/claude-3-haiku-20240307"

    def test_remove_existing(self, tmp_routes):
        rule = tmp_routes.add(pattern=RoutePattern(model="gpt-*"), target="haiku")
        removed = tmp_routes.remove(rule.id)
        assert removed is True
        assert tmp_routes.list() == []

    def test_remove_nonexistent(self, tmp_routes):
        removed = tmp_routes.remove("nonexistent-id")
        assert removed is False

    def test_multiple_rules_sorted_by_priority(self, tmp_routes):
        tmp_routes.add(pattern=RoutePattern(model="gpt-*"), target="A", priority=200)
        tmp_routes.add(pattern=RoutePattern(model="gpt-*"), target="B", priority=50)
        tmp_routes.add(pattern=RoutePattern(model="gpt-*"), target="C", priority=100)
        rules = tmp_routes.list()
        priorities = [r.priority for r in rules]
        assert priorities == sorted(priorities)

    def test_set_enabled_disable(self, tmp_routes):
        rule = tmp_routes.add(pattern=RoutePattern(model="gpt-*"), target="haiku")
        ok = tmp_routes.set_enabled(rule.id, False)
        assert ok is True
        assert tmp_routes.list()[0].enabled is False

    def test_set_enabled_nonexistent(self, tmp_routes):
        ok = tmp_routes.set_enabled("bad-id", True)
        assert ok is False

    def test_persistence_across_instances(self, tmp_path):
        path = str(tmp_path / "routes.yaml")
        store1 = RouteStore(path=path)
        rule = store1.add(pattern=RoutePattern(prefix="Hello"), target="claude-3", priority=5)

        store2 = RouteStore(path=path)
        rules = store2.list()
        assert len(rules) == 1
        assert rules[0].id == rule.id
        assert rules[0].pattern.prefix == "Hello"


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_count_tokens_approx(self):
        text = "a" * 400  # 400 chars → ~100 tokens
        assert _count_tokens_approx(text) == 100

    def test_count_tokens_approx_min(self):
        assert _count_tokens_approx("") >= 1  # never zero

    def test_extract_prompt_text_simple(self):
        payload = {
            "messages": [
                {"role": "user", "content": "Hello world"},
                {"role": "assistant", "content": "Hi there"},
            ]
        }
        text = _extract_prompt_text(payload)
        assert "Hello world" in text
        assert "Hi there" in text

    def test_extract_prompt_text_multipart(self):
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is this?"},
                        {"type": "image_url", "url": "..."},
                    ],
                }
            ]
        }
        text = _extract_prompt_text(payload)
        assert "What is this?" in text

    def test_extract_prompt_text_empty(self):
        assert _extract_prompt_text({}) == ""


# ---------------------------------------------------------------------------
# match_payload integration
# ---------------------------------------------------------------------------

class TestMatchPayload:
    def test_match_payload_routes_by_model(self, engine_with_store):
        engine, store = engine_with_store
        store.add(pattern=RoutePattern(model="gpt-4*"), target="haiku", priority=10)
        payload = {"model": "gpt-4o", "messages": [{"role": "user", "content": "test"}]}
        result = engine.match_payload(payload)
        assert result is not None
        assert result.target == "haiku"

    def test_match_payload_no_match(self, engine_with_store):
        engine, store = engine_with_store
        store.add(pattern=RoutePattern(model="gpt-4*"), target="haiku")
        payload = {"model": "claude-3-opus", "messages": [{"role": "user", "content": "test"}]}
        result = engine.match_payload(payload)
        assert result is None


# ---------------------------------------------------------------------------
# parse_pattern_args
# ---------------------------------------------------------------------------

class TestParsePatternArgs:
    def test_valid_model(self):
        pat = parse_pattern_args(model="gpt-4*")
        assert pat.model == "gpt-4*"

    def test_valid_prefix(self):
        pat = parse_pattern_args(prefix="Translate")
        assert pat.prefix == "Translate"

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="At least one"):
            parse_pattern_args()

    def test_combined(self):
        pat = parse_pattern_args(model="gpt-*", min_tokens=100, max_tokens=5000)
        assert pat.model == "gpt-*"
        assert pat.min_tokens == 100
        assert pat.max_tokens == 5000
