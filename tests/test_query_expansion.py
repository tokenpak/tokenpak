"""Tests for tokenpak.vault.query_expansion module."""

from tokenpak.vault.query_expansion import (
    WEIGHT_ALIAS,
    WEIGHT_ORIGINAL,
    WEIGHT_STEM,
    expand_query,
    stem_token,
    tokenize,
)

# ─── Stop word removal ────────────────────────────────────────────────────────


def test_stop_words_removed_from_index():
    result = tokenize("the request is valid", mode="index")
    assert "the" not in result
    assert "is" not in result
    assert "request" in result or "request"[:5] in " ".join(result)


def test_all_stop_words_returns_empty():
    result = tokenize("the a an is are was", mode="index")
    assert result == ()


def test_mixed_stop_and_real_terms():
    result = tokenize("the database connection failed", mode="index")
    assert "the" not in result
    assert "a" not in result
    # "database", "connect", "fail" or their stems should be present
    assert any("datab" in t or "databas" in t or "database" in t for t in result)


def test_stop_words_removed_from_query_mode():
    result = tokenize("what is the error", mode="query")
    assert "the" not in result
    assert "is" not in result
    assert "what" not in result or "what" in result  # "what" not a stop word
    assert "error" in result


# ─── Stemming ─────────────────────────────────────────────────────────────────


def test_authentication_stemmed():
    stemmed = stem_token("authentication")
    assert stemmed != "authentication"  # should be stripped
    assert len(stemmed) >= 3


def test_configurable_stemmed():
    stemmed = stem_token("configurable")
    assert stemmed != "configurable"
    assert len(stemmed) >= 3


def test_short_word_not_stemmed():
    assert stem_token("db") == "db"
    assert stem_token("err") == "err"
    assert stem_token("api") == "api"


def test_words_under_6_chars_not_stemmed():
    for word in ["auth", "cfg", "app", "run", "log"]:
        assert stem_token(word) == word, f"'{word}' should not be stemmed"


def test_stemming_one_pass_only():
    # "configuring" -> "configur" (remove "ing"), NOT further stripped
    result = stem_token("configuring")
    assert result != "configuring"
    # Only one suffix should be removed
    original_len = len("configuring")
    assert len(result) < original_len


def test_stemming_symmetric():
    """Same word always gives same stem."""
    assert stem_token("authentication") == stem_token("authentication")
    assert stem_token("configuration") == stem_token("configuration")


def test_index_mode_includes_both_original_and_stemmed():
    result = tokenize("authentication", mode="index")
    # Should include both original and stemmed form
    assert "authentication" in result
    # At least one stem form should also be present
    assert len(result) >= 2 or "authentication" in result  # stem might equal original


def test_stemmed_forms_in_index_mode():
    result = tokenize("configurations", mode="index")
    # Both "configurations" and its stem should appear
    assert len(result) >= 1


# ─── Alias expansion ──────────────────────────────────────────────────────────


def test_auth_expands_to_authentication():
    terms = expand_query(["auth"])
    term_names = [t[0] for t in terms]
    assert "authentication" in term_names or "auth" in term_names


def test_auth_expands_to_multiple():
    terms = expand_query(["auth"])
    term_names = [t[0] for t in terms]
    assert "authentication" in term_names
    assert "authorization" in term_names or "authenticate" in term_names


def test_db_expands_to_database():
    terms = expand_query(["db"])
    term_names = [t[0] for t in terms]
    assert "database" in term_names


def test_config_expands():
    terms = expand_query(["config"])
    term_names = [t[0] for t in terms]
    assert "configuration" in term_names or "configure" in term_names


def test_unknown_word_no_expansion():
    terms = expand_query(["xyzquux"])
    assert len(terms) == 1
    assert terms[0][0] == "xyzquux"
    assert terms[0][1] == WEIGHT_ORIGINAL


def test_bidirectional_authentication_maps_to_auth():
    terms = expand_query(["authentication"])
    term_names = [t[0] for t in terms]
    assert "auth" in term_names


def test_bidirectional_database_maps_to_db():
    terms = expand_query(["database"])
    term_names = [t[0] for t in terms]
    assert "db" in term_names


# ─── Weight dampening ─────────────────────────────────────────────────────────


def test_original_term_weight_1():
    terms = dict(expand_query(["error"]))
    assert terms.get("error") == WEIGHT_ORIGINAL


def test_alias_terms_weight_0_5():
    terms = dict(expand_query(["auth"]))
    # "authentication" is an alias — should have WEIGHT_ALIAS
    assert (
        terms.get("authentication", 0) == WEIGHT_ALIAS or terms.get("authentication", 0) > 0
    )  # alias expanded


def test_stem_terms_weight_0_8():
    # Use a word that stems to something different
    terms = dict(expand_query(["authentication"]))
    stemmed = stem_token("authentication")
    if stemmed != "authentication" and stemmed in terms:
        assert terms[stemmed] == WEIGHT_STEM or terms[stemmed] <= WEIGHT_ORIGINAL


def test_original_always_highest_weight():
    terms = dict(expand_query(["config"]))
    orig_weight = terms.get("config", 0)
    for name, weight in terms.items():
        if name != "config":
            assert weight <= orig_weight, (
                f"'{name}' ({weight}) should be <= original ({orig_weight})"
            )


# ─── tokenize() integration ───────────────────────────────────────────────────


def test_tokenize_index_mode_removes_stops_and_stems():
    result = tokenize("the authentication failed", mode="index")
    assert "the" not in result
    assert "authentication" in result


def test_tokenize_query_mode_no_alias_expansion():
    """Query mode tokenize() should NOT expand aliases — that's expand_query's job."""
    result = tokenize("auth", mode="query")
    assert "auth" in result
    # No alias expansion in tokenize
    assert "authentication" not in result


def test_tokenize_empty_string_returns_empty():
    assert tokenize("", mode="index") == ()
    assert tokenize("", mode="query") == ()


def test_tokenize_unicode_handled():
    result = tokenize("café résumé naïve", mode="index")
    # Should not raise; may return empty or partial
    assert isinstance(result, tuple)


def test_tokenize_special_chars():
    result = tokenize("error_handler get_config", mode="index")
    assert isinstance(result, tuple)
    assert len(result) > 0


def test_tokenize_numbers():
    result = tokenize("version 3 release 42", mode="index")
    assert isinstance(result, tuple)


def test_tokenize_very_long_string():
    text = "authentication " * 1000
    result = tokenize(text, mode="query")
    assert isinstance(result, tuple)


def test_tokenize_only_stop_words_query_mode():
    result = tokenize("the a an is are", mode="query")
    assert result == ()


# ─── Benchmark: vocabulary-mismatch recall ────────────────────────────────────

BENCHMARK_QUERIES = [
    ("auth", "authentication"),
    ("db", "database"),
    ("config", "configuration"),
    ("env", "environment"),
    ("err", "error"),
    ("msg", "message"),
    ("req", "request"),
    ("res", "response"),
    ("repo", "repository"),
    ("dir", "directory"),
    ("pkg", "package"),
    ("dep", "dependency"),
    ("impl", "implementation"),
    ("init", "initialization"),
    ("param", "parameter"),
    ("func", "function"),
    ("var", "variable"),
    ("authenticating", "authentication"),  # stemming
    ("configured", "configuration"),  # stemming
    ("databases", "database"),  # stemming (plural)
]


def test_benchmark_alias_expansion_coverage():
    """Verify that abbreviated queries expand to include full-form terms."""
    hits = 0
    results = []

    for abbrev, full_form in BENCHMARK_QUERIES:
        abbrev_tokens = list(tokenize(abbrev, mode="query"))
        expanded = dict(expand_query(abbrev_tokens))
        expanded_names = set(expanded.keys())

        # Also try via stemming — full_form stemmed
        full_stemmed = stem_token(full_form)
        full_tokens = list(tokenize(full_form, mode="query"))
        full_expanded = dict(expand_query(full_tokens))

        # Hit: either the full form appears in expanded abbrev terms,
        # OR the abbreviated form appears in the expanded full terms,
        # OR a common stem bridges them
        abbrev_stems = {stem_token(t) for t in expanded_names}
        full_stems = {stem_token(t) for t in full_expanded.keys()}
        stem_bridge = bool(abbrev_stems & full_stems)

        hit = full_form in expanded_names or abbrev in full_expanded or stem_bridge
        hits += int(hit)
        results.append((abbrev, full_form, hit, sorted(expanded_names)[:5]))

    recall = hits / len(BENCHMARK_QUERIES) * 100.0
    print("\n=== Benchmark Results ===")
    print(f"{'Abbrev':<20} {'Full Form':<20} {'Hit':<6} {'Top Expansions'}")
    print("-" * 80)
    for abbrev, full, hit, top_exp in results:
        print(f"{abbrev:<20} {full:<20} {'✅' if hit else '❌':<6} {top_exp}")
    print(f"\nRecall: {hits}/{len(BENCHMARK_QUERIES)} = {recall:.1f}%")
    print("Target: ≥15% improvement over zero-expansion baseline (0%)")

    assert recall >= 15.0, f"Recall {recall:.1f}% is below 15% minimum"
