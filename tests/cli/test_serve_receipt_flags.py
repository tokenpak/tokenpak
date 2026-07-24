"""Focused coverage for session-scoped ``tokenpak serve`` receipt flags."""

from __future__ import annotations

import argparse
import os
import sys
from types import ModuleType

from tokenpak._cli_core import build_parser, cmd_serve


def _serve_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "port": 8766,
        "telemetry": False,
        "ingest": False,
        "workers": 1,
        "shutdown_timeout": None,
        "safe": False,
        "profile": None,
        "stats_footer": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _fake_proxy_module(monkeypatch, observed: dict[str, object]) -> None:
    module = ModuleType("tokenpak.proxy.server")

    def start_proxy(**kwargs: object) -> None:
        observed["profile"] = os.environ.get("TOKENPAK_PROFILE")
        observed["stats_footer"] = os.environ.get("TOKENPAK_STATS_FOOTER")
        observed["kwargs"] = kwargs

    module.start_proxy = start_proxy  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tokenpak.proxy.server", module)
    monkeypatch.setattr(
        "tokenpak._cli_core._maybe_show_compression_notice",
        lambda *, safe: None,
    )


def test_serve_parser_exposes_receipt_profile_flags() -> None:
    args = build_parser().parse_args(["serve", "--profile", "aggressive", "--stats-footer"])

    assert args.profile == "aggressive"
    assert args.stats_footer is True


def test_serve_applies_overrides_before_proxy_import(monkeypatch) -> None:
    monkeypatch.delenv("TOKENPAK_PROFILE", raising=False)
    monkeypatch.delenv("TOKENPAK_STATS_FOOTER", raising=False)
    observed: dict[str, object] = {}
    _fake_proxy_module(monkeypatch, observed)

    cmd_serve(_serve_args(profile="aggressive", stats_footer=True))

    assert observed["profile"] == "aggressive"
    assert observed["stats_footer"] == "1"
    assert observed["kwargs"] == {
        "host": "127.0.0.1",
        "port": 8766,
        "blocking": True,
        "shutdown_timeout": None,
    }


def test_serve_flags_leave_defaults_and_existing_config_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("TOKENPAK_PROFILE", "agentic")
    monkeypatch.setenv("TOKENPAK_STATS_FOOTER", "0")
    observed: dict[str, object] = {}
    _fake_proxy_module(monkeypatch, observed)

    cmd_serve(_serve_args())

    assert observed["profile"] == "agentic"
    assert observed["stats_footer"] == "0"
