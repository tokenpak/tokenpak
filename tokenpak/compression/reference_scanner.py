# SPDX-License-Identifier: Apache-2.0
"""Reference Scanner — detects external references in text for compile-time injection.

Scans query + context blocks for GitHub issues/PRs, bare URLs, and
Linear/Jira ticket IDs. Returns deduplicated Reference objects.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import List


class RefType(str, Enum):
    GITHUB_ISSUE = "GITHUB_ISSUE"
    GITHUB_PR = "GITHUB_PR"
    URL = "URL"
    LINEAR_TICKET = "LINEAR_TICKET"
    JIRA_TICKET = "JIRA_TICKET"
    UNKNOWN = "UNKNOWN"


@dataclass
class Reference:
    ref_type: RefType
    raw_match: str  # Exact text that matched
    resolved_url: str  # Canonical URL to fetch


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# GitHub issue URL: https://github.com/owner/repo/issues/123
_GH_ISSUE_URL = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/issues/(\d+)",
    re.IGNORECASE,
)

# GitHub PR URL: https://github.com/owner/repo/pull/123
_GH_PR_URL = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)",
    re.IGNORECASE,
)

# Bare URL: http(s):// anything that doesn't look like an image/CDN/tracker
_BARE_URL = re.compile(
    r'https?://[^\s\)\]\}"\'<>]{8,}',
    re.IGNORECASE,
)

# URL skip patterns (images, CDN, tracking pixels, short noise)
_URL_SKIP = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|svg|ico|woff|woff2|ttf|eot|mp4|mp3|pdf)(\?|$)|"
    r"cdn\.|fonts\.googleapis|gravatar\.com|tracking\.|analytics\.|pixel\.",
    re.IGNORECASE,
)

# Linear ticket: ENG-123, TOK-42 (2–6 uppercase letters + dash + digits)
_LINEAR_TICKET = re.compile(r"\b([A-Z]{2,6})-(\d+)\b")

# Configurable Jira prefix list (Linear and Jira use the same pattern)
_JIRA_PREFIXES = {"JIRA", "PROJ", "BUG", "FEAT", "TASK", "INFRA", "OPS", "DATA"}

# Well-known Linear prefixes (to distinguish from Jira)
_LINEAR_PREFIXES = {"ENG", "TOK", "TPK", "DEV", "PROD", "SEC", "UX", "ML"}


def scan_for_references(text: str) -> List[Reference]:
    """
    Scan text for external references.

    Args:
        text: Any text (query, block content, etc.)

    Returns:
        Deduplicated list of Reference objects in order of first appearance.
    """
    refs: List[Reference] = []
    seen_urls: set = set()

    # --- GitHub issues (before bare URL so they don't get caught as URL) ---
    for m in _GH_ISSUE_URL.finditer(text):
        owner, repo, num = m.group(1), m.group(2), m.group(3)
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{num}"
        if url not in seen_urls:
            seen_urls.add(url)
            refs.append(Reference(RefType.GITHUB_ISSUE, m.group(0), url))

    # --- GitHub PRs ---
    for m in _GH_PR_URL.finditer(text):
        owner, repo, num = m.group(1), m.group(2), m.group(3)
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{num}"  # PRs use issues API
        if url not in seen_urls:
            seen_urls.add(url)
            refs.append(Reference(RefType.GITHUB_PR, m.group(0), url))

    # --- Bare URLs (skip GitHub issue/PR URLs already caught, and skip images/CDN) ---
    for m in _BARE_URL.finditer(text):
        raw = m.group(0).rstrip(".,;:!?)")
        if _URL_SKIP.search(raw):
            continue
        if "github.com" in raw and ("/issues/" in raw or "/pull/" in raw):
            continue  # Already caught above
        if raw not in seen_urls:
            seen_urls.add(raw)
            refs.append(Reference(RefType.URL, raw, raw))

    # --- Linear / Jira tickets ---
    for m in _LINEAR_TICKET.finditer(text):
        prefix, num = m.group(1), m.group(2)
        raw = m.group(0)
        if prefix in _LINEAR_PREFIXES:
            rt = RefType.LINEAR_TICKET
        elif prefix in _JIRA_PREFIXES:
            rt = RefType.JIRA_TICKET
        else:
            continue  # Unknown prefix — skip
        key = f"{rt}:{raw}"
        if key not in seen_urls:
            seen_urls.add(key)
            refs.append(Reference(rt, raw, raw))  # No URL — handled in fetcher

    return refs
