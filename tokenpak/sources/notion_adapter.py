"""Notion SourceAdapter for TokenPak.

Fetches a single Notion page by page_id via the Notion API v1.
Extracts title + all block content (paragraphs, headings, bullets, code).
API token from env var NOTION_API_TOKEN or passed directly.

No third-party deps — uses urllib.request (stdlib).
"""

import json
import os
import urllib.request
from typing import Optional, Tuple

from .base_source import Provenance, SourceAdapter, SourceFetchError

_NOTION_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_HTTP_TIMEOUT = 10


def _notion_headers(api_token: str) -> dict:
    return {
        "Authorization": f"Bearer {api_token}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _get(url: str, headers: dict) -> dict:
    """GET request → parsed JSON dict."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise SourceFetchError(f"Notion API error {exc.code} for {url}: {body[:200]}") from exc
    except Exception as exc:
        raise SourceFetchError(f"Notion request failed: {exc}") from exc


def _extract_rich_text(rich_text_list: list) -> str:
    """Flatten a Notion rich_text array to a plain string."""
    return "".join(rt.get("plain_text", "") for rt in rich_text_list)


def _block_to_text(block: dict) -> str:
    """Convert a single Notion block to markdown-like text."""
    bt = block.get("type", "")
    data = block.get(bt, {})

    if bt in ("paragraph", "quote", "callout"):
        return _extract_rich_text(data.get("rich_text", []))

    if bt in ("heading_1", "heading_2", "heading_3"):
        level = bt[-1]  # "1", "2", "3"
        text = _extract_rich_text(data.get("rich_text", []))
        return f"{'#' * int(level)} {text}"

    if bt in ("bulleted_list_item", "numbered_list_item", "to_do"):
        text = _extract_rich_text(data.get("rich_text", []))
        checked = data.get("checked", False)
        prefix = "- [x]" if (bt == "to_do" and checked) else ("- [ ]" if bt == "to_do" else "-")
        return f"{prefix} {text}"

    if bt == "code":
        lang = data.get("language", "")
        snippet = _extract_rich_text(data.get("rich_text", []))
        return f"```{lang}\n{snippet}\n```"

    if bt == "divider":
        return "---"

    if bt == "table_row":
        cells = [_extract_rich_text(c) for c in data.get("cells", [])]
        return " | ".join(cells)

    if bt == "image":
        url = data.get("external", {}).get("url", "") or data.get("file", {}).get("url", "")
        caption = _extract_rich_text(data.get("caption", []))
        return f"![{caption}]({url})"

    # Fallback: try to pull any rich_text
    rich = data.get("rich_text", [])
    if rich:
        return _extract_rich_text(rich)

    return ""


def _fetch_all_blocks(page_id: str, headers: dict) -> list:
    """Fetch all blocks for a page (handles pagination)."""
    blocks = []
    url = f"{_NOTION_API_BASE}/blocks/{page_id}/children?page_size=100"
    while url:
        data = _get(url, headers)
        blocks.extend(data.get("results", []))
        if data.get("has_more"):
            cursor = data.get("next_cursor", "")
            url = (
                f"{_NOTION_API_BASE}/blocks/{page_id}/children?start_cursor={cursor}&page_size=100"
            )
        else:
            url = None  # type: ignore[assignment]
    return blocks


def _page_title(page_data: dict) -> str:
    """Extract page title from a Notion page API response."""
    props = page_data.get("properties", {})
    # Title is usually in "title", "Name", or the first title-type prop
    for key, prop in props.items():
        if prop.get("type") == "title":
            return _extract_rich_text(prop.get("title", []))
    return ""


class NotionAdapter(SourceAdapter):
    """Fetch a single Notion page by page_id."""

    source_type = "notion"

    def __init__(self, api_token: Optional[str] = None):
        self._token = api_token or os.environ.get("NOTION_API_TOKEN", "")

    def _resolve_token(self, kwargs: dict) -> str:
        token = kwargs.get("api_token") or self._token
        if not token:
            raise SourceFetchError(
                "Notion API token required. Pass api_token= or set NOTION_API_TOKEN."
            )
        return token

    def ingest(self, source_id: str, **kwargs) -> Tuple[str, Provenance]:
        """
        Fetch a Notion page by page_id.

        Args:
            source_id:  Notion page ID (UUID with or without dashes).
            api_token:  Notion integration token (overrides env var).

        Returns:
            (content, Provenance)
        """
        page_id = source_id.replace("-", "")
        token = self._resolve_token(kwargs)
        headers = _notion_headers(token)

        # Fetch page metadata
        page_data = _get(f"{_NOTION_API_BASE}/pages/{page_id}", headers)
        title = _page_title(page_data)
        last_edited = page_data.get("last_edited_time", "")

        # Fetch all block content
        blocks = _fetch_all_blocks(page_id, headers)
        lines = [f"# {title}"] if title else []
        for block in blocks:
            text = _block_to_text(block)
            if text:
                lines.append(text)
        content = "\n\n".join(lines)

        provenance = Provenance(
            source_type=self.source_type,
            source_id=source_id,
            source_version=last_edited,
            fetched_at=self._now(),
            title=title,
        )
        return content, provenance

    def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool:
        """
        Compare last_edited_time from Notion API against cached version.
        Returns True if last_edited_time differs.
        """
        page_id = source_id.replace("-", "")
        token = self._resolve_token(kwargs)
        headers = _notion_headers(token)
        try:
            page_data = _get(f"{_NOTION_API_BASE}/pages/{page_id}", headers)
            last_edited = page_data.get("last_edited_time", "")
            return last_edited != cached_version
        except SourceFetchError:
            return False  # Assume unchanged on error
