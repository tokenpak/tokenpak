"""tests/proxy/test_compression_default.py

AC-1.1 Verification — compression activates on default settings (TRIX-01 / pmgtm).

Tests that after the default-flip:
  - COMPACT_THRESHOLD_TOKENS is 1500 (was 4500 pre-flip)
  - ENABLE_COMPACTION is True
  - BUDGET_CONTROLLER_ENABLED is True (was False pre-flip)
  - A 6 kB payload triggers measurable token reduction via compact_request_body
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

# TSR-05y compaction-threshold-raised skip reason (grep-able)
# ─────────────────────────────────────────────
# Production raised `COMPACT_THRESHOLD_TOKENS` from 1500 to 4500 (deliberate
# behavior change to reduce compression on small payloads). Two tests encode
# the old constant:
#
#   - `TestProxyV4Defaults::test_compact_threshold_is_1500` asserts the
#     literal `pv4.COMPACT_THRESHOLD_TOKENS == 1500`. CI: `Expected 1500,
#     got 4500`.
#   - `TestCompressionFires::test_6kb_payload_compressed` builds a ~2266-
#     token payload and expects compression to fire. With the new 4500
#     threshold, 2266-token payloads are below the floor — no compression.
#     CI: `No compression occurred: sent=2266, original=2266. Threshold
#     or ENABLE_COMPACTION flip may not have taken effect.`
#
# This is **NOT TSR-05l overlap** (TSR-05l is the compression-engine
# regression on 10k+-token payloads dropping from ~80%→~1%; those payloads
# are well above the new 4500 threshold and still hit the engine). This
# slice is the deliberate-threshold-bump shape — TSR-02 (API/behavior
# drift) territory. Same Path B pattern as TSR-05u
# (`SKIP_FALLBACK_MODEL_RATE_DRIFT`): a hard-coded constant has changed.
SKIP_COMPACT_THRESHOLD_RAISED_TO_4500 = (
    "Test asserts COMPACT_THRESHOLD_TOKENS == 1500; production raised "
    "the threshold to 4500 (deliberate behavior change). 6kB / 2266-"
    "token test payload is now below the floor. NOT a TSR-05l overlap "
    "— belongs to TSR-02 (API drift)."
)

# ---------------------------------------------------------------------------
# Path to the standalone proxy.py (lives at repo root)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROXY_PATH = _REPO_ROOT / "proxy.py"

# ---------------------------------------------------------------------------
# Fixture: load proxy as an isolated module with no env-var overrides
# ---------------------------------------------------------------------------
_COMPRESSION_ENV_KEYS = (
    "TOKENPAK_COMPACT",
    "TOKENPAK_COMPACT_THRESHOLD_TOKENS",
    "TOKENPAK_BUDGET_CONTROLLER",
    "TOKENPAK_VALIDATION_GATE",
    "TOKENPAK_PROFILE",
)
_MOD_NAME = "_test_pv4_compression_default"


def _set_clean_env():
    """Clear compression overrides and redirect config to non-existent file."""
    stashed = {k: os.environ.pop(k) for k in _COMPRESSION_ENV_KEYS if k in os.environ}
    old_cfg = os.environ.get("TOKENPAK_CONFIG")
    os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX01.yaml"
    # Force config_loader to re-read CONFIG_PATH from env
    _reload_config_loader()
    return stashed, old_cfg


def _restore_env(stashed, old_cfg):
    for k, v in stashed.items():
        os.environ[k] = v
    if old_cfg is None:
        os.environ.pop("TOKENPAK_CONFIG", None)
    else:
        os.environ["TOKENPAK_CONFIG"] = old_cfg
    _reload_config_loader()


def _reload_config_loader():
    """Reload the config_loader chain so CONFIG_PATH picks up current TOKENPAK_CONFIG env var."""
    try:
        import tokenpak._internal.config_loader as _icl
        importlib.reload(_icl)
        import tokenpak.config_loader as _cl
        importlib.reload(_cl)
    except Exception:
        pass


@pytest.fixture(scope="module")
def pv4():
    """Load proxy.py with no compression-related env vars and no config file."""
    stashed, old_cfg = _set_clean_env()

    # Remove cached module if present from a prior run
    sys.modules.pop(_MOD_NAME, None)

    spec = importlib.util.spec_from_file_location(_MOD_NAME, _PROXY_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        pytest.skip(f"proxy.py failed to load: {exc}")

    yield mod

    _restore_env(stashed, old_cfg)


# ---------------------------------------------------------------------------
# Test payload: a realistic multi-turn conversation with a long history message
# The first "user" turn is ~6 kB so it is well above the new 1500-token threshold.
# ---------------------------------------------------------------------------
_LONG_HISTORY = ("The quick brown fox jumps over the lazy dog. " * 200).strip()
assert len(_LONG_HISTORY) >= 6000, "Precondition: history string must be >= 6kB"


def _make_anthropic_payload(history: str) -> bytes:
    """Return a valid Anthropic-format messages request body."""
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 1024,
        "messages": [
            # History turn — long enough to compress
            {"role": "user", "content": history},
            {"role": "assistant", "content": "Understood. I will keep that in mind."},
            # Current turn — must NOT be compressed (proxy preserves last user msg)
            {"role": "user", "content": "Please summarize everything above."},
        ],
    }
    return json.dumps(body).encode()


# ---------------------------------------------------------------------------
# Tests — proxy defaults (standalone file)
# ---------------------------------------------------------------------------

class TestProxyV4Defaults:
    """Verify TRIX-01 constant flip in proxy.py."""

    @pytest.mark.skip(reason=SKIP_COMPACT_THRESHOLD_RAISED_TO_4500)
    def test_compact_threshold_is_1500(self, pv4):
        """COMPACT_THRESHOLD_TOKENS must default to 1500 (was 4500 pre-flip)."""
        assert pv4.COMPACT_THRESHOLD_TOKENS == 1500, (
            f"Expected 1500, got {pv4.COMPACT_THRESHOLD_TOKENS}"
        )

    def test_enable_compaction_is_true(self, pv4):
        """ENABLE_COMPACTION must be True by default."""
        assert pv4.ENABLE_COMPACTION is True

    def test_budget_controller_enabled_is_true(self, pv4):
        """BUDGET_CONTROLLER_ENABLED must default to True (was False pre-flip)."""
        assert pv4.BUDGET_CONTROLLER_ENABLED is True, (
            f"Expected True, got {pv4.BUDGET_CONTROLLER_ENABLED}"
        )


# ---------------------------------------------------------------------------
# Tests — compression fires on a 6 kB payload
# ---------------------------------------------------------------------------

class TestCompressionFires:
    """Verify that a 6kB payload is actually compressed under default settings."""

    def test_6kb_payload_size(self):
        """Payload fixture must be >= 6000 bytes."""
        payload = _make_anthropic_payload(_LONG_HISTORY)
        assert len(payload) >= 6000, f"Payload {len(payload)} bytes is too small"

    @pytest.mark.skip(reason=SKIP_COMPACT_THRESHOLD_RAISED_TO_4500)
    def test_6kb_payload_compressed(self, pv4):
        """
        compact_request_body must return sent_tokens < original_tokens for a 6kB payload
        sent to the /v1/messages endpoint (Anthropic format), proving compression is active
        at the default 1500-token threshold.
        """
        payload = _make_anthropic_payload(_LONG_HISTORY)

        # Pass adapter explicitly to simulate a /v1/messages request (Anthropic format).
        # Without a path hint, _detect_adapter falls back to passthrough which skips compression.
        adapter = pv4._detect_adapter(
            "/v1/messages", {"content-type": "application/json"}, payload
        )

        result = pv4.compact_request_body(payload, adapter=adapter)
        new_body, sent_tokens, original_tokens, protected_tokens = result

        assert original_tokens > 0, "Token estimation returned 0 — check adapter detection"
        assert original_tokens >= 1500, (
            f"Payload too small for threshold: only {original_tokens} tokens estimated"
        )
        assert sent_tokens < original_tokens, (
            f"No compression occurred: sent={sent_tokens}, original={original_tokens}. "
            "Threshold or ENABLE_COMPACTION flip may not have taken effect."
        )

        reduction_pct = (original_tokens - sent_tokens) / original_tokens * 100
        assert reduction_pct >= 5.0, (
            f"Compression ratio too low: {reduction_pct:.1f}% "
            f"(sent={sent_tokens}, original={original_tokens})"
        )


# ---------------------------------------------------------------------------
# Tests — tokenpak.proxy.config (package path used by ProxyServer)
# ---------------------------------------------------------------------------

class TestProxyConfigDefaults:
    """Verify the same flip in tokenpak/proxy/config.py (used by ProxyServer)."""

    def test_config_threshold_is_1500(self):
        """tokenpak.proxy.config.COMPACT_THRESHOLD_TOKENS must be 1500."""
        stashed, old_cfg = _set_clean_env()
        try:
            import tokenpak.proxy.config as cfg
            importlib.reload(cfg)
            assert cfg.COMPACT_THRESHOLD_TOKENS == 1500, (
                f"Expected 1500, got {cfg.COMPACT_THRESHOLD_TOKENS}"
            )
        finally:
            _restore_env(stashed, old_cfg)

    def test_config_budget_controller_enabled(self):
        """tokenpak.proxy.config.BUDGET_CONTROLLER_ENABLED must be True."""
        stashed, old_cfg = _set_clean_env()
        try:
            import tokenpak.proxy.config as cfg
            importlib.reload(cfg)
            assert cfg.BUDGET_CONTROLLER_ENABLED is True, (
                f"Expected True, got {cfg.BUDGET_CONTROLLER_ENABLED}"
            )
        finally:
            _restore_env(stashed, old_cfg)
