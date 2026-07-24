# SPDX-License-Identifier: Apache-2.0
"""Reference Fetcher — fetches content for detected references.

Handles GitHub issues/PRs via REST API and bare URLs via URLAdapter.
Linear/Jira return None (stubs). All failures are silent.
"""

import json
import os
import urllib.request
from typing import Optional

from tokenpak.sources.base_source import SourceFetchError as URLFetchError
from tokenpak.sources.url_adapter import URLAdapter

from .reference_scanner import Reference, RefType

_GITHUB_API = "https://api.github.com"
_FETCH_TIMEOUT = 5  # seconds
_MAX_COMMENTS = 3  # first N comments included

_url_adapter = URLAdapter()


# ---------------------------------------------------------------------------
# GitHub fetcher
# ---------------------------------------------------------------------------


def _github_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "TokenPak/1.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _gh_get(url: str) -> Optional[object]:
    """GET a GitHub API URL; return parsed JSON or None on error."""
    try:
        req = urllib.request.Request(url, headers=_github_headers())
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            parsed: object = json.loads(resp.read().decode())
            return parsed
    except Exception:
        return None


def _string_keyed_dict(value: object) -> Optional[dict[str, object]]:
    """Return a string-keyed view of a decoded JSON object."""
    if not isinstance(value, dict):
        return None
    return {key: item for key, item in value.items() if isinstance(key, str)}


def _joined_fields(value: object, field_name: str) -> str:
    """Join string fields from a decoded JSON array of objects."""
    if not isinstance(value, list):
        return ""
    fields: list[str] = []
    for item in value:
        mapping = _string_keyed_dict(item)
        if mapping is None:
            continue
        field = mapping.get(field_name)
        if isinstance(field, str):
            fields.append(field)
    return ", ".join(fields)


def _format_issue(data: dict[str, object], comments: list[dict[str, object]]) -> str:
    """Format a GitHub issue/PR as readable text."""
    title = data.get("title", "")
    state = data.get("state", "")
    body_value = data.get("body")
    body = body_value.strip()[:2000] if isinstance(body_value, str) else ""
    labels = _joined_fields(data.get("labels"), "name")
    assignees = _joined_fields(data.get("assignees"), "login")
    number = data.get("number", "")
    url = data.get("html_url", "")

    lines = [
        f"## #{number}: {title}",
        f"**State:** {state}",
    ]
    if labels:
        lines.append(f"**Labels:** {labels}")
    if assignees:
        lines.append(f"**Assignees:** {assignees}")
    if url:
        lines.append(f"**URL:** {url}")
    if body:
        lines.append(f"\n### Description\n{body}")

    if comments:
        lines.append("\n### Comments")
        for c in comments[:_MAX_COMMENTS]:
            user = _string_keyed_dict(c.get("user"))
            login = user.get("login") if user is not None else None
            author = login if isinstance(login, str) else "unknown"
            comment_body = c.get("body")
            cbody = comment_body.strip()[:500] if isinstance(comment_body, str) else ""
            lines.append(f"**@{author}:** {cbody}")

    return "\n".join(lines)


def _fetch_github(ref: Reference) -> Optional[str]:
    """Fetch a GitHub issue or PR via the REST API."""
    if not os.environ.get("GITHUB_TOKEN"):
        # Warn but still try — public repos work without token (rate-limited)
        import sys

        print("[ref-inject] GITHUB_TOKEN not set — rate limits apply", file=sys.stderr)

    data = _string_keyed_dict(_gh_get(ref.resolved_url))
    if data is None or "title" not in data:
        return None

    # Fetch comments if issue has any
    comments: list[dict[str, object]] = []
    comments_url = data.get("comments_url")
    comment_count = data.get("comments", 0)
    if (
        isinstance(comments_url, str)
        and isinstance(comment_count, (int, float))
        and comment_count > 0
    ):
        raw = _gh_get(f"{comments_url}?per_page={_MAX_COMMENTS}&page=1")
        if isinstance(raw, list):
            comments = [item for value in raw if (item := _string_keyed_dict(value)) is not None]

    return _format_issue(data, comments)


# ---------------------------------------------------------------------------
# URL fetcher (delegates to URLAdapter)
# ---------------------------------------------------------------------------


def _fetch_url(ref: Reference) -> Optional[str]:
    """Fetch a bare URL via the URL adapter."""
    try:
        content, _ = _url_adapter.ingest(ref.resolved_url)
        return content[:5000] if content else None  # Cap at 5k chars
    except (URLFetchError, Exception):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_reference(ref: Reference) -> Optional[str]:
    """
    Fetch content for a detected reference.

    Args:
        ref: Reference object from reference_scanner.

    Returns:
        Content string if fetch succeeded, None on failure or unsupported type.
    """
    try:
        if ref.ref_type in (RefType.GITHUB_ISSUE, RefType.GITHUB_PR):
            return _fetch_github(ref)
        elif ref.ref_type == RefType.URL:
            return _fetch_url(ref)
        elif ref.ref_type in (RefType.LINEAR_TICKET, RefType.JIRA_TICKET):
            import sys

            print(
                f"[ref-inject] {ref.ref_type} ({ref.raw_match}) not yet supported",
                file=sys.stderr,
            )
            return None
        return None
    except Exception:
        return None  # Always fail silently
