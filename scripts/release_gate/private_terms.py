#!/usr/bin/env python3
"""Hashed register of private terms for the public-safe content gates.

Personal names and private host handles must never appear in this repository,
and that includes the checks that keep them out. The register therefore holds
salted hashes, not the words: every word in the scanned text is hashed and
compared. The hash only keeps the words out of the source; it is not a secret,
so do not rely on it to protect anything beyond that.

To add a term, compute its hash locally and add the hex value to the right
set. Never commit the word itself::

    python3 scripts/release_gate/private_terms.py --hash <word>

Scan files (exit 1 on any hit)::

    python3 scripts/release_gate/private_terms.py FILE [FILE ...]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

SALT = "tokenpak:"

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def term_hash(word: str) -> str:
    """FNV-1a (32-bit) over the salted, lower-cased word, as 8 hex digits."""
    h = 0x811C9DC5
    for byte in (SALT + str(word).lower()).encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


# Register: personal-name parts (first name, surname) and private host handles.
PRIVATE_TERMS: dict[str, frozenset[str]] = {
    "name": frozenset({"a5e86eb1", "5753e335"}),
    "handle": frozenset({"0400be0c"}),
}


@dataclass(frozen=True)
class Hit:
    kind: str
    word: str
    index: int


def classify_term(word: str, terms: dict[str, frozenset[str]] | None = None) -> str | None:
    """Return the register kind for one word, or None."""
    reg = PRIVATE_TERMS if terms is None else terms
    h = term_hash(word)
    for kind, hashes in reg.items():
        if h in hashes:
            return kind
    return None


def _is_whole_word(text: str, start: int, word: str) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[start + len(word)] if start + len(word) < len(text) else ""
    return not re.match(r"[A-Za-z0-9_]", before or " ") and after != "_"


def find_private_terms(text: str, terms: dict[str, frozenset[str]] | None = None) -> list[Hit]:
    """Return every private term found in ``text``."""
    hits: list[Hit] = []
    for m in _WORD.finditer(text):
        if not _is_whole_word(text, m.start(), m.group(0)):
            continue
        kind = classify_term(m.group(0), terms)
        if kind:
            hits.append(Hit(kind, m.group(0), m.start()))
    return hits


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--hash":
        for w in args[1:]:
            print(term_hash(w))
        return 0
    fail = 0
    for path in args:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().split("\n")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines, 1):
            for hit in find_private_terms(line):
                print(f"{path}:{i}: private {hit.kind}")
                print(f"::error file={path},line={i}::Private {hit.kind} on a public surface.")
                fail = 1
    return fail


if __name__ == "__main__":
    raise SystemExit(main())
