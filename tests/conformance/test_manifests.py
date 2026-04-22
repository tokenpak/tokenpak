"""Manifest artifact validation.

The two canonical manifest files shipped under tokenpak/manifests/
(SC-04) must:

1. validate against their registry schemas (provider-profile /
   client-profile);
2. have capabilities arrays byte-identical to the frozensets in
   tokenpak.core.contracts.capabilities (single canonical source);
3. be reachable via importlib.resources so installed wheels can load
   them without knowing the source-tree layout.
"""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

import pytest

from tokenpak_tip_validator import validate_against

from tokenpak.core.contracts.capabilities import (
    SELF_CAPABILITIES_COMPANION,
    SELF_CAPABILITIES_PROXY,
)


pytestmark = pytest.mark.conformance


def _load_manifest(name: str) -> dict[str, Any]:
    path = files("tokenpak").joinpath(f"manifests/{name}")
    return json.loads(path.read_text(encoding="utf-8"))


def test_proxy_manifest_validates_against_provider_profile():
    manifest = _load_manifest("tokenpak-proxy.json")
    res = validate_against("provider-profile", manifest)
    assert res.ok, (
        f"tokenpak-proxy.json failed provider-profile: "
        f"{[(f.code, f.message) for f in res.errors()]}"
    )


def test_companion_manifest_validates_against_client_profile():
    manifest = _load_manifest("tokenpak-companion.json")
    res = validate_against("client-profile", manifest)
    assert res.ok, (
        f"tokenpak-companion.json failed client-profile: "
        f"{[(f.code, f.message) for f in res.errors()]}"
    )


def test_proxy_manifest_capabilities_sync_with_canonical():
    """DECISION-SC-04: manifest capabilities array == SELF_CAPABILITIES_PROXY.
    Drift here means the manifest lies about what the proxy actually
    publishes. SC-01 SELF_CAPABILITIES_PROXY is the authoritative
    source; this file is the publishable artifact. Both must agree.
    """
    manifest = _load_manifest("tokenpak-proxy.json")
    manifest_caps = frozenset(manifest["capabilities"])
    assert manifest_caps == SELF_CAPABILITIES_PROXY, (
        f"Proxy manifest capabilities drift:\n"
        f"  manifest only: {sorted(manifest_caps - SELF_CAPABILITIES_PROXY)}\n"
        f"  canonical only: {sorted(SELF_CAPABILITIES_PROXY - manifest_caps)}"
    )


def test_companion_manifest_capabilities_sync_with_canonical():
    manifest = _load_manifest("tokenpak-companion.json")
    manifest_caps = frozenset(manifest["capabilities"])
    assert manifest_caps == SELF_CAPABILITIES_COMPANION, (
        f"Companion manifest capabilities drift:\n"
        f"  manifest only: {sorted(manifest_caps - SELF_CAPABILITIES_COMPANION)}\n"
        f"  canonical only: {sorted(SELF_CAPABILITIES_COMPANION - manifest_caps)}"
    )


def test_both_manifests_share_tip_version():
    """Pin TIP version against drift between the two files."""
    proxy = _load_manifest("tokenpak-proxy.json")
    companion = _load_manifest("tokenpak-companion.json")
    assert proxy["tip_version"] == companion["tip_version"] == "TIP-1.0"
