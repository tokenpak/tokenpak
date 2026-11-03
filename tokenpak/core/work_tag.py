"""Optional job attribution, independent of spend-guard agent identity.

Tags are opaque client-owned attribution values. Invalid
values are ignored with a single warning that never echoes untrusted input.
"""

import logging
import re

WORK_TAG_HEADER = "X-Tokenpak-Tag"
_JOB_TAG = re.compile(r"[A-Za-z0-9._:-]{1,128}", re.ASCII)
_log = logging.getLogger(__name__)


def validate_work_tag(value: object) -> str:
    """Return a safe tag (at most 128 characters), or the empty sentinel."""
    if value is None or value == "":
        return ""
    if isinstance(value, str) and len(value) <= 128 and _JOB_TAG.fullmatch(value):
        return value
    _log.warning(
        "Ignoring malformed TokenPak job tag; expected 1-128 letters, digits, dots, underscores, colons or hyphens"
    )
    return ""


def work_tag_from_headers(headers: object) -> str:
    """Read only the job header; never reinterpret it as an agent identity."""
    for key, value in headers.items():
        if key.lower() == WORK_TAG_HEADER.lower():
            return validate_work_tag(value)
    return ""


_JOB_PATH = "/_tokenpak/t/"


def tagged_proxy_url(base_url: str, tag: str) -> str:
    """Attach a validated tag using a proxy-local path, not provider overrides."""
    from urllib.parse import urlsplit, urlunsplit

    if not tag:
        return base_url
    parsed = urlsplit(base_url)
    segment = tag.replace(".", "%2E") if tag in {".", ".."} else tag
    return urlunsplit(parsed._replace(path=f"{_JOB_PATH}{segment}{parsed.path}"))


def extract_job_path(path: str) -> tuple[str, str]:
    """Remove a job prefix before route selection; malformed tags never block."""
    if not path.startswith(_JOB_PATH):
        return path, ""
    pieces = path[len(_JOB_PATH) :].split("/", 1)
    if len(pieces) < 2:
        validate_work_tag("invalid/tag")
        return path, ""
    from urllib.parse import unquote

    tag = validate_work_tag(unquote(pieces[0]))
    return "/" + pieces[1], tag
