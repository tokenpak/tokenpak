# SPDX-License-Identifier: Apache-2.0
"""Privacy-bounded retrieval of TokenPak's public PyPI version metadata."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from packaging.version import InvalidVersion, Version

PYPI_VERSION_METADATA_URL = "https://pypi.org/pypi/tokenpak/json"
_MAX_RESPONSE_BYTES = 1024 * 1024


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep the constitutionally permitted request on its exact endpoint."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise urllib.error.HTTPError(
            req.full_url,
            code,
            "PyPI update metadata redirected outside the exact request contract",
            headers,
            fp,
        )


def fetch_latest_pypi_version(timeout: float = 5.0) -> str:
    """Fetch and validate the latest public TokenPak version from PyPI.

    The request is a bodyless HTTPS GET to one exact URL. Redirects are
    rejected, the response is bounded, and the returned version must be valid
    PEP 440. Callers own consent, caching, and failure presentation.
    """

    request = urllib.request.Request(
        PYPI_VERSION_METADATA_URL,
        data=None,
        headers={"Accept": "application/json"},
        method="GET",
    )
    opener = urllib.request.build_opener(_RejectRedirects())
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != PYPI_VERSION_METADATA_URL:
            raise ValueError("PyPI update metadata response URL changed")
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("PyPI update metadata response is too large")

    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("PyPI update metadata is not an object")
    info = payload.get("info")
    if not isinstance(info, dict):
        raise ValueError("PyPI update metadata has no info object")
    value = info.get("version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PyPI update metadata has no version")
    try:
        return str(Version(value.strip()))
    except InvalidVersion as exc:
        raise ValueError("PyPI update metadata has an invalid version") from exc
