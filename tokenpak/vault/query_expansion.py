"""Re-export from tokenpak.vault.query_expansion for compatibility."""
from tokenpak.vault.query_expansion import *
from tokenpak.vault.query_expansion import (
    ALIASES, STOP_WORDS, SUFFIX_RULES,
    WEIGHT_ALIAS, WEIGHT_ORIGINAL, WEIGHT_STEM,
    expand_query, stem_token, tokenize, get_query_terms_with_weights,
)
