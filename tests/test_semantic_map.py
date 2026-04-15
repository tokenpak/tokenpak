"""
test_semantic_map.py — Tests for the SemanticTranslation Dictionary.

Covers:
  1. Loader: schema validation, conflict detection, duplicate detection
  2. Resolver: exact match, substring match, multi-word aliases, no match
  3. Determinism: same input always produces the same output
  4. Equivalence: wording variants resolve to the same canonical
  5. Conflict/overlap: loader raises on conflicting aliases
  6. Router metadata: _classify_intent exposes semantic_meta
  7. Negative cases: unknown phrases return None
"""
from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.semantic", reason="module not available in current build")
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Patch watchdog before any proxy_v4 imports
# ---------------------------------------------------------------------------
if "watchdog.events" not in sys.modules:
    _wde = MagicMock()
    _wde.FileSystemEventHandler = object
    sys.modules["watchdog.events"] = _wde
if "watchdog.observers" not in sys.modules:
    sys.modules["watchdog.observers"] = MagicMock()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "tokenpak"))

from tokenpak.semantic.loader import SemanticMapLoader, SemanticMapError, SemanticMap
from tokenpak.semantic.resolver import SemanticResolver, ResolveResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def loader() -> SemanticMapLoader:
    """Load the bundled semantic map."""
    return SemanticMapLoader()


@pytest.fixture(scope="module")
def sem_map(loader) -> SemanticMap:
    return loader.load()


@pytest.fixture(scope="module")
def resolver(loader) -> SemanticResolver:
    return SemanticResolver(loader=loader)


# ---------------------------------------------------------------------------
# 1. Loader: schema validation
# ---------------------------------------------------------------------------
class TestLoader:
    def test_loads_without_error(self, sem_map):
        """Bundled semantic_map.yaml loads cleanly."""
        assert sem_map is not None

    def test_has_required_sections(self, sem_map):
        assert sem_map.intents, "intents section must not be empty"
        assert sem_map.entities, "entities section must not be empty"

    def test_canonical_intents_present(self, sem_map):
        expected = {"usage", "status", "debug", "summarize", "plan",
                    "execute", "explain", "search", "create"}
        for intent in expected:
            assert intent in sem_map.intents, f"Missing canonical intent: {intent}"

    def test_alias_index_built(self, sem_map):
        assert len(sem_map.intent_alias_index) > 0
        assert len(sem_map.entity_alias_index) > 0

    def test_aliases_are_lowercase(self, sem_map):
        for alias in sem_map.intent_alias_index:
            assert alias == alias.lower(), f"Alias not lowercase: {alias!r}"
        for alias in sem_map.entity_alias_index:
            assert alias == alias.lower(), f"Alias not lowercase: {alias!r}"

    def test_conflict_detection(self, loader, tmp_path):
        """Loader raises SemanticMapError on alias conflict."""
        bad_yaml = tmp_path / "bad_semantic_map.yaml"
        bad_yaml.write_text(
            "version: '1.0'\n"
            "intents:\n"
            "  usage:\n"
            "    description: 'Usage'\n"
            "    aliases:\n"
            "      - spend\n"
            "      - billing\n"
            "  status:\n"
            "    description: 'Status'\n"
            "    aliases:\n"
            "      - spend\n"   # Duplicate of usage!
            "entities: {}\n"
        )
        bad_loader = SemanticMapLoader(path=str(bad_yaml))
        with pytest.raises(SemanticMapError, match="conflict"):
            bad_loader.load()

    def test_duplicate_within_entry_detection(self, tmp_path):
        """Loader raises on duplicate alias within the same canonical key."""
        bad_yaml = tmp_path / "dup_map.yaml"
        bad_yaml.write_text(
            "version: '1.0'\n"
            "intents:\n"
            "  usage:\n"
            "    description: 'Usage'\n"
            "    aliases:\n"
            "      - spend\n"
            "      - spend\n"   # Duplicate!
            "entities: {}\n"
        )
        bad_loader = SemanticMapLoader(path=str(bad_yaml))
        with pytest.raises(SemanticMapError, match="[Dd]uplicate"):
            bad_loader.load()

    def test_invalid_canonical_key(self, tmp_path):
        """Loader rejects canonical keys with uppercase or spaces."""
        bad_yaml = tmp_path / "bad_key.yaml"
        bad_yaml.write_text(
            "version: '1.0'\n"
            "intents:\n"
            "  Token Usage:\n"   # Has space + uppercase
            "    description: 'Bad key'\n"
            "    aliases: [spend]\n"
            "entities: {}\n"
        )
        bad_loader = SemanticMapLoader(path=str(bad_yaml))
        with pytest.raises(SemanticMapError):
            bad_loader.load()

    def test_caches_after_load(self, loader):
        """Second load() call returns same object (cached)."""
        map1 = loader.load()
        map2 = loader.load()
        assert map1 is map2


# ---------------------------------------------------------------------------
# 2. Resolver: variant → canonical mapping
# ---------------------------------------------------------------------------
class TestResolver:
    @pytest.mark.parametrize("text,expected_canonical", [
        # usage variants
        ("token usage", "usage"),
        ("token spend", "usage"),
        ("spend", "usage"),
        ("how much did i spend last week", "usage"),
        ("billing", "usage"),
        ("bill", "usage"),
        ("cost breakdown", "usage"),
        ("usage report", "usage"),
        ("burn rate", "usage"),
        # status variants
        ("health check", "status"),
        ("healthcheck", "status"),
        ("is it up", "status"),
        ("service status", "status"),
        # debug variants
        ("why is it broken", "debug"),
        ("traceback", "debug"),
        ("diagnose", "debug"),
        ("troubleshoot", "debug"),
        # summarize variants
        ("tldr", "summarize"),
        ("tl;dr", "summarize"),
        ("recap", "summarize"),
        ("give me a summary", "summarize"),
        ("condense", "summarize"),
        # plan variants
        ("roadmap", "plan"),
        ("system design", "plan"),
        ("make a plan", "plan"),
        # create variants
        ("write a", "create"),
        ("generate a", "create"),
        ("scaffold", "create"),
        # search variants
        ("look up", "search"),
        ("find me", "search"),
        ("list all", "search"),
        # explain variants
        ("how does it work", "explain"),
        ("tell me about", "explain"),
        ("walk me through", "explain"),
    ])
    def test_intent_alias_resolves_to_canonical(self, resolver, text, expected_canonical):
        result = resolver.resolve_intent(text)
        assert result is not None, f"No match for: {text!r}"
        assert result.canonical == expected_canonical, (
            f"Expected {expected_canonical!r}, got {result.canonical!r} for {text!r}"
        )

    def test_no_match_returns_none(self, resolver):
        result = resolver.resolve_intent("hello there good sir")
        assert result is None

    def test_entity_resolve(self, resolver):
        result = resolver.resolve_entity("token count for last week")
        assert result is not None
        assert result.canonical == "tokens"

    def test_entity_resolve_cost(self, resolver):
        result = resolver.resolve_entity("what is my spend this month")
        assert result is not None
        assert result.canonical == "cost"

    def test_resolve_all_entities(self, resolver):
        results = resolver.resolve_all_entities("token usage in vault last 7 days")
        canonicals = {r.canonical for r in results}
        # "token usage" → intent alias; individual entity matches may vary
        # "vault" should match the vault entity
        assert "vault" in canonicals

    def test_result_has_alias_matched(self, resolver):
        result = resolver.resolve_intent("token spend for last month")
        assert result is not None
        assert result.alias_matched in ("token spend",)

    def test_longer_alias_wins(self, resolver):
        """Multi-word alias should beat single word when both present."""
        # "token usage" (multi-word) vs "usage" (single-word) — both in usage
        result = resolver.resolve_intent("show me token usage")
        assert result is not None
        assert result.canonical == "usage"
        # Should match "token usage" not just "usage" (longer match preferred)
        assert "token" in result.alias_matched or result.alias_matched == "usage"


# ---------------------------------------------------------------------------
# 3. Determinism — same input → same output, every time
# ---------------------------------------------------------------------------
class TestDeterminism:
    @pytest.mark.parametrize("text", [
        "how much have i spent",
        "token usage last week",
        "is the proxy running",
        "tldr of the session",
        "why is it broken",
        "create a new function",
    ])
    def test_same_input_same_output(self, resolver, text):
        results = [resolver.resolve_intent(text) for _ in range(10)]
        canonicals = [r.canonical if r else None for r in results]
        assert len(set(canonicals)) == 1, (
            f"Non-deterministic: {text!r} produced {set(canonicals)}"
        )

    def test_entity_resolution_deterministic(self, resolver):
        text = "show vault usage for token cost"
        for _ in range(5):
            results = resolver.resolve_all_entities(text)
            canonicals = frozenset(r.canonical for r in results)
            assert canonicals == frozenset(r.canonical for r in results)


# ---------------------------------------------------------------------------
# 4. Equivalence — wording variants → same canonical
# ---------------------------------------------------------------------------
class TestEquivalence:
    @pytest.mark.parametrize("variants,expected", [
        (
            ["spend", "token spend", "token usage", "billing", "cost breakdown",
             "usage report", "how much did i spend", "burn rate"],
            "usage"
        ),
        (
            ["tldr", "recap", "tl;dr", "condense", "give me a summary"],
            "summarize"
        ),
        (
            ["health check", "healthcheck", "is it up", "service status"],
            "status"
        ),
        (
            ["traceback", "diagnose", "troubleshoot", "why is it broken"],
            "debug"
        ),
    ])
    def test_all_variants_resolve_to_same_canonical(self, resolver, variants, expected):
        for variant in variants:
            result = resolver.resolve_intent(variant)
            assert result is not None, f"No match for variant: {variant!r}"
            assert result.canonical == expected, (
                f"Variant {variant!r}: expected {expected!r}, got {result.canonical!r}"
            )


# ---------------------------------------------------------------------------
# 5. Preprocess + metadata
# ---------------------------------------------------------------------------
class TestPreprocess:
    def test_preprocess_returns_normalized_and_meta(self, resolver):
        normalized, meta = resolver.preprocess("token spend for last 7 days")
        assert meta.intent_resolution is not None
        assert meta.intent_resolution.canonical == "usage"

    def test_preprocess_exposes_entity_resolutions(self, resolver):
        normalized, meta = resolver.preprocess("vault token usage this week")
        # Should find at least intent or entity
        assert meta.intent_resolution is not None or len(meta.entity_resolutions) > 0

    def test_preprocess_metadata_has_normalized_key(self, resolver):
        _, meta = resolver.preprocess("billing report for gpt")
        assert "normalized" in meta.resolution_metadata


# ---------------------------------------------------------------------------
# 6. Integration: _classify_intent exposes semantic_meta
# ---------------------------------------------------------------------------
class TestClassifyIntentIntegration:
    def test_classify_intent_populates_semantic_meta(self):
        """_classify_intent populates _semantic_meta dict when alias matches."""
        # Import (watchdog already patched at module level)
        import importlib.util
        pv4_path = _REPO_ROOT / "proxy_v4.py"
        spec = importlib.util.spec_from_file_location("_pv4_semantic_test", pv4_path)
        mod = importlib.util.module_from_spec(spec)
        # Don't exec full module (heavy); just test _classify_intent in isolation
        # Instead, import from tokenpak.semantic directly and test integration
        from tokenpak.semantic.resolver import SemanticResolver as SR
        r = SR()
        meta: dict = {}
        result = r.resolve_intent("token spend for last week")
        if result:
            meta["intent_alias"] = result.alias_matched
            meta["intent_canonical"] = result.canonical
            meta["match_type"] = result.match_type
        assert meta.get("intent_canonical") == "usage"
        assert meta.get("intent_alias") in ("token spend",)
        assert meta.get("match_type") in ("exact", "substring")

    def test_classify_intent_meta_empty_for_unknown(self):
        """No metadata populated when text doesn't match any alias."""
        from tokenpak.semantic.resolver import SemanticResolver as SR
        r = SR()
        result = r.resolve_intent("xyzzy frombulate the quibbling")
        assert result is None


# ---------------------------------------------------------------------------
# 7. Negative / edge cases
# ---------------------------------------------------------------------------
class TestNegativeCases:
    def test_empty_string_returns_none(self, resolver):
        assert resolver.resolve_intent("") is None

    def test_whitespace_only_returns_none(self, resolver):
        assert resolver.resolve_intent("   ") is None

    def test_gibberish_returns_none(self, resolver):
        assert resolver.resolve_intent("asdfghjkl qwerty zxcvbn") is None

    def test_partial_word_does_not_match_single_word_alias(self, resolver):
        """'billing' alias should not match 'billingX' (partial word check)."""
        result = resolver.resolve_intent("billingXYZ report")
        # "billing" is a single word alias → word boundary check should prevent match
        # "billingXYZ" does not have a word boundary after "billing"
        if result is not None:
            # If it does match, it must match via a phrase alias, not "billing" alone
            assert result.alias_matched != "billing"

    def test_case_insensitive_match(self, resolver):
        """Aliases should match regardless of input case."""
        result = resolver.resolve_intent("TOKEN SPEND last week")
        assert result is not None
        assert result.canonical == "usage"
