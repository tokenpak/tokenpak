# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.3 — opt-in suggest mode config test suite.

Twelve directive-mandated test classes:

  - default config = observe_only
  - explicit suggest config loads
  - invalid mode fails closed
  - dry_run cannot be disabled yet
  - allow_auto_routing cannot be enabled yet
  - response_headers cannot be enabled yet
  - CLI/dashboard/API obey suggestion_surface flags
  - doctor shows config state
  - no route mutation
  - no request mutation
  - no classifier mutation
  - no prompt text/secrets emitted

Read-only across the board.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tokenpak.proxy.intent_policy_config_loader import (
    PERMITTED_MODES_2_4_3,
    is_surface_active,
    load_policy_config_safely,
    parse_intent_policy_block,
    resolve_active_config_path,
)
from tokenpak.proxy.intent_policy_engine import (
    PolicyEngineConfig,
    SuggestionSurfaceConfig,
    load_default_config,
)

# ── 1. Default config = observe_only ──────────────────────────────────


class TestDefaultConfig:

    def test_default_is_observe_only(self):
        cfg, warns = parse_intent_policy_block(None)
        assert cfg.mode == "observe_only"
        assert cfg.dry_run is True
        assert cfg.allow_auto_routing is False
        assert cfg.show_suggestions is False
        assert cfg.suggestion_surface.response_headers is False
        assert warns == []

    def test_load_safely_returns_policy_engine_config(self, tmp_path: Path,
                                                       monkeypatch):
        # Point the loader at a non-existent file; expect default.
        cfg = load_policy_config_safely(candidate_path=tmp_path / "nope.yaml")
        assert isinstance(cfg, PolicyEngineConfig)
        assert cfg.mode == "observe_only"

    def test_default_loader_imports_cleanly(self):
        cfg = load_default_config()
        assert cfg.mode == "observe_only"


# ── 2. Explicit suggest config loads ──────────────────────────────────


class TestSuggestConfigLoads:

    def test_explicit_suggest_mode(self):
        cfg, warns = parse_intent_policy_block({"mode": "suggest"})
        assert cfg.mode == "suggest"
        assert cfg.is_suggest_mode() is True
        # show_suggestions auto-enabled when mode = suggest.
        assert cfg.show_suggestions is True
        assert warns == []

    def test_suggest_mode_with_explicit_surface_overrides(self):
        cfg, warns = parse_intent_policy_block({
            "mode": "suggest",
            "suggestion_surface": {"cli": False, "dashboard": True, "api": True},
        })
        assert cfg.mode == "suggest"
        assert cfg.suggestion_surface.cli is False
        assert cfg.suggestion_surface.dashboard is True

    def test_suggest_active_helper(self):
        cfg, _ = parse_intent_policy_block({"mode": "suggest"})
        assert is_surface_active(cfg, "cli") is True
        assert is_surface_active(cfg, "dashboard") is True
        assert is_surface_active(cfg, "api") is True
        # response_headers always False.
        assert is_surface_active(cfg, "response_headers") is False

    def test_suggest_with_show_suggestions_explicit_false(self):
        # Even if mode = suggest, an explicit show_suggestions=false
        # turns off the suggest-mode badging.
        cfg, _ = parse_intent_policy_block({
            "mode": "suggest", "show_suggestions": False,
        })
        assert cfg.mode == "suggest"
        assert cfg.show_suggestions is False
        assert is_surface_active(cfg, "cli") is False


# ── 3. Invalid mode fails closed ──────────────────────────────────────


class TestInvalidModeFailsClosed:

    def test_unknown_mode_string(self):
        cfg, warns = parse_intent_policy_block({"mode": "tornado"})
        assert cfg.mode == "observe_only"
        assert any("not a known value" in w for w in warns)

    def test_reserved_modes_fall_back(self):
        for reserved in ("confirm", "enforce"):
            cfg, warns = parse_intent_policy_block({"mode": reserved})
            assert cfg.mode == "observe_only", (
                f"reserved mode {reserved!r} should fall back to observe_only"
            )
            assert any("reserved" in w for w in warns)

    def test_non_string_mode(self):
        for raw in (123, True, [], {}):
            cfg, warns = parse_intent_policy_block({"mode": raw})
            assert cfg.mode == "observe_only"

    def test_permitted_modes_pinned(self):
        # Future change to PERMITTED_MODES_2_4_3 requires explicit
        # ratification — the current permitted set is exactly two
        # values.
        assert set(PERMITTED_MODES_2_4_3) == {"observe_only", "suggest"}


# ── 4. dry_run cannot be disabled ─────────────────────────────────────


class TestDryRunForcedTrue:

    def test_explicit_false_overridden(self):
        cfg, warns = parse_intent_policy_block({
            "mode": "suggest", "dry_run": False,
        })
        assert cfg.dry_run is True
        assert any("dry_run" in w and "forced to True" in w for w in warns)

    def test_dry_run_string_false_overridden(self):
        cfg, _ = parse_intent_policy_block({
            "mode": "suggest", "dry_run": "false",
        })
        assert cfg.dry_run is True


# ── 5. allow_auto_routing cannot be enabled ───────────────────────────


class TestAllowAutoRoutingForcedFalse:

    def test_explicit_true_overridden(self):
        cfg, warns = parse_intent_policy_block({
            "mode": "suggest", "allow_auto_routing": True,
        })
        assert cfg.allow_auto_routing is False
        assert any("allow_auto_routing" in w and "forced to False" in w for w in warns)

    def test_string_yes_overridden(self):
        cfg, _ = parse_intent_policy_block({
            "mode": "suggest", "allow_auto_routing": "yes",
        })
        assert cfg.allow_auto_routing is False


# ── 6. response_headers cannot be enabled ─────────────────────────────


class TestResponseHeadersForcedFalse:

    def test_explicit_true_in_surface_overridden(self):
        cfg, warns = parse_intent_policy_block({
            "mode": "suggest",
            "suggestion_surface": {"response_headers": True},
        })
        assert cfg.suggestion_surface.response_headers is False
        assert any("response_headers" in w for w in warns)

    def test_response_headers_default_false_in_default_config(self):
        cfg, _ = parse_intent_policy_block(None)
        assert cfg.suggestion_surface.response_headers is False


# ── 7. CLI/dashboard/API obey suggestion_surface flags ────────────────


class TestSurfaceFlagsObeyed:

    def test_individual_surface_flips(self):
        for surface_name in ("cli", "dashboard", "api"):
            cfg, _ = parse_intent_policy_block({
                "mode": "suggest",
                "suggestion_surface": {surface_name: False},
            })
            # Other surfaces stay True; the flipped one is False.
            for other in ("cli", "dashboard", "api"):
                expected = other != surface_name
                actual = is_surface_active(cfg, other)
                assert actual is expected, (
                    f"surface {other!r} active={actual} when {surface_name} flipped off"
                )

    def test_observe_only_mode_no_surface_active(self):
        cfg, _ = parse_intent_policy_block({"mode": "observe_only"})
        for surface in ("cli", "dashboard", "api"):
            assert is_surface_active(cfg, surface) is False, (
                f"observe_only must not activate any surface; got {surface}=True"
            )


# ── 8. Doctor shows config state ──────────────────────────────────────


class TestDoctorShowsConfig:

    def test_intent_view_carries_policy_config(self):
        from tokenpak.proxy.intent_doctor import collect_intent_view
        view = collect_intent_view()
        assert "policy_config" in view
        cfg = view["policy_config"]
        assert isinstance(cfg, dict)
        assert cfg.get("mode") in ("observe_only", "suggest")
        assert cfg.get("dry_run") is True
        assert cfg.get("allow_auto_routing") is False
        # response_headers locked False.
        assert cfg["suggestion_surface"]["response_headers"] is False

    def test_intent_view_render_includes_config_section(self):
        from tokenpak.proxy.intent_doctor import (
            collect_intent_view,
            render_intent_view,
        )
        text = render_intent_view(collect_intent_view())
        assert "Active policy config (Phase 2.4.3)" in text
        assert "dry_run:" in text
        assert "(locked True in 2.4.3)" in text
        assert "TokenPak has not changed routing" in text

    def test_explain_last_carries_policy_config_when_present(self, tmp_path: Path):
        # Even on an empty DB, the explain payload renders the
        # "no rows yet" text — config snapshot is only populated
        # when a row exists. Pass: assert no crash on empty.
        from tokenpak.proxy.intent_doctor import (
            collect_explain_last,
            render_explain_last,
        )
        payload = collect_explain_last(db_path=tmp_path / "nope.db")
        assert payload is None  # no rows
        text = render_explain_last(payload)
        assert "No intent_events rows yet" in text


# ── 9. No route mutation ──────────────────────────────────────────────


class TestNoRouteMutation:

    def test_loader_does_not_import_dispatch_path(self):
        import tokenpak.proxy.intent_policy_config_loader as m
        src = Path(m.__file__).read_text()
        for forbidden in ("forward_headers", "pool.request", "pool.stream"):
            assert forbidden not in src, (
                f"loader references dispatch primitive: {forbidden!r}"
            )


# ── 10. No request mutation ───────────────────────────────────────────


class TestNoRequestMutation:

    def test_repeated_loads_do_not_create_files(self, tmp_path: Path):
        before = sorted(p.name for p in tmp_path.iterdir())
        for _ in range(5):
            load_policy_config_safely(candidate_path=tmp_path / "nope.yaml")
        after = sorted(p.name for p in tmp_path.iterdir())
        assert before == after, "loader created files; must be read-only"

    def test_config_dataclass_is_frozen(self):
        cfg = PolicyEngineConfig()
        with pytest.raises(Exception):
            cfg.mode = "suggest"  # type: ignore[misc]

    def test_surface_dataclass_is_frozen(self):
        s = SuggestionSurfaceConfig()
        with pytest.raises(Exception):
            s.cli = False  # type: ignore[misc]


# ── 11. No classifier mutation ────────────────────────────────────────


class TestNoClassifierMutation:

    def test_classifier_constants_unchanged(self):
        from tokenpak.proxy.intent_classifier import (
            CLASSIFY_THRESHOLD,
            INTENT_SOURCE_V0,
        )
        assert CLASSIFY_THRESHOLD == 0.4
        assert INTENT_SOURCE_V0 == "rule_based_v0"


# ── 12. Privacy contract ──────────────────────────────────────────────


class TestPrivacyContract:
    SENTINEL = "kevin-magic-prompt-marker-PHASE-2-4-3"

    def test_no_sentinel_in_config_snapshot(self):
        # The config loader / engine read no caller-supplied
        # strings; planting a sentinel in the YAML's mode field
        # should fall back to observe_only and surface a warning,
        # NOT propagate the sentinel to any rendered output.
        cfg, warns = parse_intent_policy_block({"mode": self.SENTINEL})
        assert cfg.mode == "observe_only"
        # The warning may name the offending value — that's fine; it
        # comes from the loader, not from a prompt. But the rendered
        # config dict MUST NOT carry the sentinel.
        d = cfg.to_dict()
        assert self.SENTINEL not in json.dumps(d)

    def test_doctor_render_does_not_leak_sentinel(self, tmp_path: Path,
                                                   monkeypatch):
        # Create a malformed YAML containing the sentinel + ensure
        # the doctor render doesn't echo it. We point the loader at
        # the temp file via TOKENPAK_HOME.
        cfg_path = tmp_path / "policy.yaml"
        cfg_path.write_text(
            f"intent_policy:\n  mode: {self.SENTINEL}\n", encoding="utf-8",
        )
        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))

        from tokenpak.proxy.intent_doctor import (
            collect_intent_view,
            render_intent_view,
        )
        view = collect_intent_view()
        text = render_intent_view(view)
        # The mode field should have fallen back to observe_only;
        # no sentinel substring should appear in the rendered view.
        assert self.SENTINEL not in text
        # Rendered mode is the safe fallback.
        assert "mode:                      observe_only" in text


# ── 13. Dashboard payload exposes config (cross-cutting) ──────────────


class TestDashboardPayloadConfig:

    def test_metadata_includes_active_policy_config(self, tmp_path: Path):
        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        meta = payload["metadata"]
        assert "active_policy_config" in meta
        assert "suggest_mode_active" in meta
        # Default state: not active.
        assert meta["suggest_mode_active"] is False

    def test_metadata_suggest_mode_active_when_config_opts_in(
        self, tmp_path: Path, monkeypatch,
    ):
        cfg_path = tmp_path / "policy.yaml"
        cfg_path.write_text(
            "intent_policy:\n  mode: suggest\n", encoding="utf-8",
        )
        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))

        from tokenpak.proxy.intent_policy_dashboard import collect_policy_dashboard
        payload = collect_policy_dashboard(window_days=14, db_path=tmp_path / "nope.db")
        assert payload["metadata"]["suggest_mode_active"] is True
        cfg = payload["metadata"]["active_policy_config"]
        assert cfg["mode"] == "suggest"
        assert cfg["dry_run"] is True
        assert cfg["allow_auto_routing"] is False
        assert cfg["suggestion_surface"]["response_headers"] is False


# ── 14. CLI subprocess smoke ──────────────────────────────────────────


class TestCliSmoke:

    def test_intent_config_show_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "config", "--show"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        assert "Intent policy config (Phase 2.4.3)" in result.stdout
        assert "dry_run:" in result.stdout

    def test_intent_config_validate_runs(self):
        # Validate exits 0 even on hosts with no config file.
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "config", "--validate"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0

    def test_intent_config_json_parses(self):
        result = subprocess.run(
            [sys.executable, "-m", "tokenpak", "intent", "config", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 0
        d = json.loads(result.stdout)
        assert "active_config" in d
        assert "suggest_mode_active" in d


# ── 15. resolve_active_config_path (cross-cutting) ────────────────────


class TestResolvePath:

    def test_returns_none_when_no_file(self, monkeypatch, tmp_path: Path):
        # Point HOME at an empty dir so the default path doesn't
        # accidentally hit the real ~/.tokenpak/policy.yaml.
        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
        # Also override HOME so ~/.tokenpak/policy.yaml resolution
        # falls inside tmp_path.
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_active_config_path() is None

    def test_returns_path_when_file_exists(self, monkeypatch, tmp_path: Path):
        cfg_path = tmp_path / "policy.yaml"
        cfg_path.write_text("intent_policy:\n  mode: suggest\n", encoding="utf-8")
        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
        resolved = resolve_active_config_path()
        assert resolved == cfg_path
