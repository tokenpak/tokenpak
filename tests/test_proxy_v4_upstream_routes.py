import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import proxy_v4


def _write_openclaw(tmp_path: Path, payload: dict) -> Path:
    cfg_path = tmp_path / ".openclaw" / "openclaw.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")
    return cfg_path


def test_load_overrides_reads_models_providers(monkeypatch, tmp_path):
    _write_openclaw(
        tmp_path,
        {
            "models": {
                "providers": {
                    "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
                    "tokenpak-openrouter": {},
                    "litellm": {"baseUrl": "http://litellm.local:4000"},
                    "tokenpak-litellm": {},
                    "bedrock": {"base_url": "https://bedrock-runtime.us-east-1.amazonaws.com"},
                    "tokenpak-bedrock": {},
                    "vercel-ai-gateway": {"base_url": "https://ai-gateway.vercel.sh/v1"},
                    "tokenpak-vercel-ai-gateway": {},
                    "kilocode": {"base_url": "https://api.kilocode.ai/v1"},
                    "tokenpak-kilocode": {},
                }
            },
            "providers": {
                "openrouter": {"base_url": "https://legacy-should-not-win.example"},
                "tokenpak-openrouter": {},
            },
        },
    )
    monkeypatch.setattr(proxy_v4.Path, "home", lambda: tmp_path)

    overrides = proxy_v4._load_openclaw_upstream_overrides()

    assert overrides["openai-chat"] == "https://ai-gateway.vercel.sh/v1"
    assert overrides["openai-responses"] == "https://api.kilocode.ai/v1"
    assert overrides["anthropic-messages"] == "https://bedrock-runtime.us-east-1.amazonaws.com"


def test_load_overrides_falls_back_to_legacy_root_providers(monkeypatch, tmp_path):
    _write_openclaw(
        tmp_path,
        {
            "providers": {
                "openrouter": {"base_url": "https://openrouter.ai/api/v1"},
                "tokenpak-openrouter": {},
            }
        },
    )
    monkeypatch.setattr(proxy_v4.Path, "home", lambda: tmp_path)

    overrides = proxy_v4._load_openclaw_upstream_overrides()

    assert overrides["openai-chat"] == "https://openrouter.ai/api/v1"
    assert overrides["openai-responses"] == "https://openrouter.ai/api/v1"


def test_resolve_upstream_passthrough_requires_explicit_mapping(monkeypatch):
    adapter = next(a for a in proxy_v4.ADAPTER_REGISTRY.adapters() if a.source_format == "passthrough")
    monkeypatch.setattr(proxy_v4, "UPSTREAM_ROUTES", {})

    with pytest.raises(ValueError, match="No upstream route mapping for passthrough"):
        proxy_v4._resolve_upstream(adapter)


def test_resolve_upstream_passthrough_uses_configured_route(monkeypatch):
    adapter = next(a for a in proxy_v4.ADAPTER_REGISTRY.adapters() if a.source_format == "passthrough")
    monkeypatch.setattr(proxy_v4, "UPSTREAM_ROUTES", {"passthrough": "https://proxy.example"})

    assert proxy_v4._resolve_upstream(adapter) == "https://proxy.example"
