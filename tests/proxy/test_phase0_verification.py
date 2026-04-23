# SPDX-License-Identifier: Apache-2.0
"""A2 + A4 (PM/GTM v2 Phase 0): verify-only smokes.

Preflight (see initiative `CLAIMS-VS-REALITY-2026-04-23.md`) confirmed both
A2 compression-defaults-on AND A4 dashboard-mount are already shipped. This
file is a drift guard: if either property regresses between Phase 0 Day 1
(preflight) and Phase 0 closeout (Day 5), this test fails and the gate
holds.

These are verify-only. If either fails, DO NOT silently fix — it is a
real regression; surface to Sue + promote to an execution packet.

Traces to v2 M-A2 + M-A4 per
~/vault/02_COMMAND_CENTER/initiatives/2026-04-23-tokenpak-pm-gtm-readiness-v2/.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# A2: compression enabled by default
# ---------------------------------------------------------------------------


def test_compression_enabled_by_default(monkeypatch):
    """A2: with no env overrides, compression.enabled defaults True.

    `get_all()` is the canonical flat-dict accessor (builds `result` with
    all defaults + env overrides at ``loader.py:97-120``). Preflight cited
    ``compression.enabled`` default True at loader.py:110. If the default
    flips, fresh `tokenpak serve` stops compressing and README's 30–50%
    claim silently becomes untrue.
    """
    # Scrub env so the test measures defaults, not ambient state.
    monkeypatch.delenv("TOKENPAK_COMPACT", raising=False)

    from tokenpak.core.config.loader import get_all

    cfg = get_all()
    assert cfg.get("compression.enabled") is True, (
        "A2 regression: compression.enabled is not True by default. "
        f"Measured: {cfg.get('compression.enabled')!r}. This would silently "
        "disable compression on fresh installs, breaking README's 30-50% claim."
    )


def test_compression_threshold_is_nontrivial(monkeypatch):
    """A2: threshold must be a positive integer — zero/unset would mis-compress everything."""
    monkeypatch.delenv("TOKENPAK_COMPACT_THRESHOLD_TOKENS", raising=False)

    from tokenpak.core.config.loader import get_all

    cfg = get_all()
    threshold = cfg.get("compression.threshold_tokens", 0)
    assert isinstance(threshold, int) and threshold > 0, (
        f"A2 regression: compression.threshold_tokens should be positive int, got {threshold!r}"
    )


# ---------------------------------------------------------------------------
# A4: dashboard is mounted
# ---------------------------------------------------------------------------


def test_dashboard_handler_returns_valid_html_for_root():
    """A4: serve_dashboard_file('/') returns HTML content + a mime type.

    Preflight cited tokenpak/proxy/server.py:346-358 as the routing point
    and tokenpak/dashboard/__init__.py:32-62 as the file server. If the
    handler stops returning HTML, the `/dashboard` endpoint 404s on fresh
    installs and README's claim of a working dashboard on the proxy port
    silently breaks.
    """
    import asyncio

    from tokenpak.dashboard import serve_dashboard_file

    result = asyncio.run(serve_dashboard_file("/"))
    assert result is not None, (
        "A4 regression: serve_dashboard_file('/') returned None. "
        "The dashboard is no longer mounted on the proxy port."
    )

    content, mime_type = result
    assert isinstance(content, str) and len(content) > 0, (
        f"A4 regression: dashboard root returned empty content (mime={mime_type!r})"
    )
    assert "text/html" in mime_type.lower(), (
        f"A4 regression: dashboard root returned non-HTML mime: {mime_type!r}"
    )
    # Sanity: the page includes something that looks like an HTML document.
    lower = content.lower()
    assert "<html" in lower or "<!doctype" in lower or "<title" in lower, (
        "A4 regression: dashboard root body doesn't look like HTML"
    )
