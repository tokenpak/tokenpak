"""
tests/proxy/test_passthrough.py

Regression test for TRIX-MTC-07 Fix #6:
  PassthroughConfig.__post_init__() must raise ValueError when strip_headers
  and safe_to_log share any element.

Before the fix, constructing a contradictory config (strip AND log the same
header) silently succeeded, producing undefined forwarding behavior.
"""

from __future__ import annotations

import pytest

from tokenpak.proxy.passthrough import PassthroughConfig


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------

def test_passthrough_config_rejects_overlapping_headers():
    """
    A header listed in both strip_headers and safe_to_log must cause ValueError.
    """
    with pytest.raises(ValueError, match="strip_headers and safe_to_log"):
        PassthroughConfig(
            strip_headers={"content-length", "authorization"},
            safe_to_log={"content-type", "authorization"},  # overlap: authorization
        )


def test_passthrough_config_rejects_multiple_overlapping_headers():
    """
    Multiple overlapping headers must all be mentioned in the error.
    """
    with pytest.raises(ValueError) as exc_info:
        PassthroughConfig(
            strip_headers={"host", "connection", "user-agent"},
            safe_to_log={"user-agent", "connection", "content-type"},
        )
    msg = str(exc_info.value)
    assert "connection" in msg or "user-agent" in msg


def test_passthrough_config_valid_no_overlap():
    """
    A config with disjoint strip_headers and safe_to_log must construct cleanly.
    """
    cfg = PassthroughConfig(
        strip_headers={"host", "connection", "content-length"},
        safe_to_log={"content-type", "user-agent"},
    )
    assert "host" in cfg.strip_headers
    assert "content-type" in cfg.safe_to_log


def test_passthrough_config_default_is_valid():
    """
    The default PassthroughConfig() must have no overlap (no regression).
    """
    cfg = PassthroughConfig()  # must not raise
    overlap = cfg.strip_headers & cfg.safe_to_log
    assert not overlap, f"Default config has overlapping headers: {overlap}"


def test_passthrough_config_empty_sets_are_valid():
    """
    Empty sets are trivially disjoint and must construct cleanly.
    """
    cfg = PassthroughConfig(strip_headers=set(), safe_to_log=set())
    assert cfg.strip_headers == set()
    assert cfg.safe_to_log == set()
