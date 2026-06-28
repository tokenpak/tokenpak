# SPDX-License-Identifier: Apache-2.0
"""Version-probe trust regression tests (ratified curated-1.9.4 item).

The expected proxy version must be derived from ``tokenpak.__version__`` (not a
separate hardcoded literal that drifts), and the live probe must read ``/health``
(which carries ``version``) rather than ``/version``.
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import MagicMock, patch

import tokenpak
import tokenpak._cli_core as _cli_core


def test_expected_proxy_version_tracks_package_version():
    """PROXY_VERSION must equal the installed package version."""
    assert _cli_core.PROXY_VERSION == tokenpak.__version__


def test_no_stale_hardcoded_proxy_version_literal():
    """The stale ``PROXY_VERSION = "1.1.0"`` literal must not return."""
    src = inspect.getsource(_cli_core)
    assert 'PROXY_VERSION = "1.1.0"' not in src


def test_version_probe_uses_health_endpoint():
    """`_get_proxy_version` must probe /health (which carries version), not /version."""
    captured = {}

    class _FakeResp:
        def read(self):
            return json.dumps({"status": "ok", "version": tokenpak.__version__}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(url, *a, **k):
        captured["url"] = url
        return _FakeResp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        result = _cli_core._get_proxy_version()

    assert "/health" in captured.get("url", "")
    assert "/version" not in captured.get("url", "")
    assert result.get("version") == tokenpak.__version__
