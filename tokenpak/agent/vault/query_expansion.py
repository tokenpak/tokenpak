"""Query expansion for BM25 vault search — stop words, stemming, aliases."""

import re
from functools import lru_cache
from typing import List, Tuple

__all__ = ["STOP_WORDS", "SUFFIX_RULES", "ALIASES", "stem_token", "expand_query", "tokenize"]

# Stop words — removed from both index and query terms
STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "i", "you", "he",
    "she", "we", "they", "it", "its", "this", "that", "these", "those",
    "and", "or", "not", "but", "if", "for", "in", "on", "at", "to",
    "of", "by", "with", "from", "as", "about", "into", "through",
})

# Suffix stripping rules — (suffix, replacement). ONE pass only, min 6 chars.
SUFFIX_RULES: List[Tuple[str, str]] = [
    ("ation", ""), ("ment", ""), ("ness", ""), ("tion", ""), ("sion", ""),
    ("able", ""), ("ible", ""), ("ful", ""), ("less", ""), ("ous", ""),
    ("ive", ""), ("ing", ""), ("ly", ""), ("er", ""), ("ed", ""), ("es", ""), ("s", ""),
]

# Synonym aliases — bidirectional. Expanded at QUERY TIME only.
ALIASES = {
    "auth": ["authentication", "authorization", "authenticate"],
    "config": ["configuration", "configure", "settings"],
    "db": ["database"], "err": ["error", "exception"], "env": ["environment"],
    "msg": ["message"], "req": ["request"], "res": ["response"],
    "repo": ["repository"], "dir": ["directory"], "pkg": ["package"],
    "dep": ["dependency", "dependencies"], "impl": ["implementation", "implement"],
    "init": ["initialization", "initialize"], "param": ["parameter"],
    "args": ["arguments"], "func": ["function"], "var": ["variable"],
    "val": ["value"], "k8s": ["kubernetes"], "tf": ["terraform"],
    "ci": ["continuous", "integration"], "cd": ["continuous", "deployment"],
    # Reverse mappings
    "authentication": ["auth"], "authorization": ["auth"], "database": ["db"],
    "configuration": ["config"], "configure": ["config"], "error": ["err"],
    "exception": ["err"], "environment": ["env"], "message": ["msg"],
    "request": ["req"], "response": ["res"], "repository": ["repo"],
    "directory": ["dir"], "package": ["pkg"], "dependency": ["dep"],
    "dependencies": ["dep"], "implementation": ["impl"], "implement": ["impl"],
    "initialization": ["init"], "initialize": ["init"], "parameter": ["param"],
    "arguments": ["args"], "function": ["func"], "variable": ["var"],
    "kubernetes": ["k8s"], "terraform": ["tf"],
}

# Weight constants
WEIGHT_ORIGINAL = 1.0
WEIGHT_ALIAS = 0.5
WEIGHT_STEM = 0.8
MIN_STEM_LENGTH = 6


def stem_token(word: str) -> str:
    """Apply suffix stemming rules (min 6 chars, one pass only)."""
    if len(word) < MIN_STEM_LENGTH:
        return word
    for suffix, replacement in SUFFIX_RULES:
        if word.endswith(suffix):
            stemmed = word[:-len(suffix)] + replacement
            if len(stemmed) >= 3:
                return stemmed
            return word
    return word


def expand_query(tokens: List[str]) -> List[Tuple[str, float]]:
    """Expand query tokens with aliases and stems, returning weighted terms."""
    term_weights: dict = {}
    for token in tokens:
        if token not in term_weights or term_weights[token] < WEIGHT_ORIGINAL:
            term_weights[token] = WEIGHT_ORIGINAL
        stemmed = stem_token(token)
        if stemmed != token:
            if stemmed not in term_weights or term_weights[stemmed] < WEIGHT_STEM:
                term_weights[stemmed] = WEIGHT_STEM
        if token in ALIASES:
            for alias in ALIASES[token]:
                if alias not in term_weights or term_weights[alias] < WEIGHT_ALIAS:
                    term_weights[alias] = WEIGHT_ALIAS
                alias_stemmed = stem_token(alias)
                if alias_stemmed != alias:
                    weight = min(WEIGHT_ALIAS, WEIGHT_STEM)
                    if alias_stemmed not in term_weights or term_weights[alias_stemmed] < weight:
                        term_weights[alias_stemmed] = weight
    return list(term_weights.items())


@lru_cache(maxsize=512)
def tokenize(text: str, mode: str = "index") -> Tuple[str, ...]:
    """Tokenize text with stop-word removal and stemming.

    mode="index": returns BOTH original and stemmed forms (for inverted index)
    mode="query": returns original tokens only (use expand_query() for weights)
    """
    raw_tokens = re.findall(r"[a-z0-9_]+", text.lower())
    filtered = [t for t in raw_tokens if t not in STOP_WORDS]

    if mode == "index":
        result = set()
        for token in filtered:
            result.add(token)
            stemmed = stem_token(token)
            if stemmed != token:
                result.add(stemmed)
        return tuple(sorted(result))
    else:  # query mode
        return tuple(filtered)


def get_query_terms_with_weights(query: str) -> List[Tuple[str, float]]:
    """Convenience: tokenize query and expand with weights for BM25 scoring."""
    tokens = list(tokenize(query, mode="query"))
    return expand_query(tokens)
