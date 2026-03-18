"""Regex/heuristic patterns for deterministic entity extraction."""

from __future__ import annotations

import re

# Basic path detection: unix, home-relative, and windows-ish
FILE_PATH_RE = re.compile(
    r"(?P<path>(?:~?/|/)[\w./-]+|[A-Za-z]:\\[\\\w .-]+(?:\\[\\\w .-]+)*)"
)

API_ENDPOINT_RE = re.compile(
    r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s+)?(?P<path>/[A-Za-z0-9_./{}:-]+)"
)

DATE_RE = re.compile(
    r"\b(?:(?:20\d{2})-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])/(?:20\d{2})"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},\s*20\d{2})\b",
    re.IGNORECASE,
)

DECISION_RE = re.compile(
    r"\b(?:decision|decided|we will|we should|approved|rejected)\b[:\- ]*(?P<text>.+)",
    re.IGNORECASE,
)

GLOSSARY_RE = re.compile(
    r"\b(?:term|glossary)\s*[:\-]\s*(?P<term>[A-Za-z][\w -]{1,60})",
    re.IGNORECASE,
)

CONFIG_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\b")

PERSON_RE = re.compile(r"\b([A-Z][a-z]+\s+[A-Z][a-z]+)\b")

ORG_RE = re.compile(
    r"\b([A-Z][A-Za-z0-9& ]{2,}(?:Inc|LLC|Corp|Corporation|Ltd|Systems|Technologies))\b"
)

FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
