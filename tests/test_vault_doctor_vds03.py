# SPDX-License-Identifier: Apache-2.0
"""Tests for VDS-03 — ``tokenpak doctor`` stale vault-index check.

Covers Component 3 of `2026-04-28-tokenpak-vault-directory-scheduling`:

* fresh (within threshold) → pass
* stale (last_indexed older than expected_interval × 2) → warn
* missing path on disk → warn
* never indexed (no last_indexed metadata) → warn (auto schedule) / pass (manual)
* manual schedule with old timestamp → still pass (no age-based warn)
* corrupt timestamp → warn
* last_index_status != ok → warn
* unreadable / corrupt vault.yaml → single graceful warn (does not fail other checks)
* doctor integration: stale-index warn shows up in --json output
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokenpak.vault import config as vault_config
from tokenpak.vault import doctor_check

HOUR = 3600
DAY = 24 * HOUR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """Render a UTC datetime in the same Z-suffixed form VDS-01 writes."""
    return dt.replace(microsecond=0).astimezone(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _make_cfg(tmp_path: Path, **entry_kwargs) -> vault_config.VaultConfig:
    """Build a single-entry config rooted under tmp_path."""
    proj = tmp_path / entry_kwargs.pop("dir_name", "proj")
    proj.mkdir(parents=True, exist_ok=True)
    entry_kwargs.setdefault("path", str(proj))
    return vault_config.VaultConfig(paths=[vault_config.VaultPathEntry(**entry_kwargs)])


# ---------------------------------------------------------------------------
# Fresh / stale / threshold logic
# ---------------------------------------------------------------------------


def test_fresh_path_within_threshold_passes(tmp_path):
    """An index rebuilt 1h ago against a 6h interval is fresh → pass."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="every 6 hours",
        expected_interval_seconds=6 * HOUR,
        last_indexed=_iso(now - timedelta(hours=1)),
        last_index_status="ok",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert len(findings) == 1
    assert findings[0].status == "ok"
    assert findings[0].severity == "pass"
    # Age computed correctly
    assert findings[0].age_seconds == pytest.approx(HOUR, abs=1)
    # Threshold = expected × 2
    assert findings[0].threshold_seconds == 12 * HOUR


def test_stale_path_past_double_interval_warns(tmp_path):
    """At expected_interval × 2 + 1s, the path is stale → warn."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="every 6 hours",
        expected_interval_seconds=6 * HOUR,
        last_indexed=_iso(now - timedelta(hours=12, seconds=1)),
        last_index_status="ok",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert len(findings) == 1
    f = findings[0]
    assert f.status == "stale"
    assert f.severity == "warn"
    assert "stale" in f.message.lower()
    assert f.age_seconds is not None and f.age_seconds > f.threshold_seconds


def test_at_exactly_threshold_is_still_fresh(tmp_path):
    """Boundary: age == threshold should NOT warn (spec says ``>`` not ``>=``)."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="every 6 hours",
        expected_interval_seconds=6 * HOUR,
        last_indexed=_iso(now - timedelta(hours=12)),
        last_index_status="ok",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "ok"
    assert findings[0].severity == "pass"


def test_missing_expected_interval_uses_24h_fallback(tmp_path):
    """No expected_interval_seconds → 24h fallback × 2 = 48h threshold."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="every 6 hours",  # grammar present, but interval not yet computed
        expected_interval_seconds=None,
        last_indexed=_iso(now - timedelta(hours=23)),  # under fallback × 2 (48h)
        last_index_status="ok",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "ok"
    assert findings[0].threshold_seconds == 48 * HOUR

    # ...same path, but now 49h old → stale.
    cfg2 = _make_cfg(
        tmp_path,
        dir_name="proj2",
        schedule="every 6 hours",
        expected_interval_seconds=None,
        last_indexed=_iso(now - timedelta(hours=49)),
        last_index_status="ok",
    )
    findings2 = doctor_check.check_vault_paths(cfg2, now=now)
    assert findings2[0].status == "stale"


# ---------------------------------------------------------------------------
# Missing / never indexed
# ---------------------------------------------------------------------------


def test_missing_path_warns_regardless_of_schedule(tmp_path):
    """A registered directory that no longer exists on disk → warn."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = vault_config.VaultConfig(
        paths=[
            vault_config.VaultPathEntry(
                path=str(tmp_path / "deleted-dir"),
                schedule="manual",  # even manual triggers warn for missing path
                last_indexed=_iso(now - timedelta(minutes=1)),
                last_index_status="ok",
            )
        ]
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "missing"
    assert findings[0].severity == "warn"
    assert "missing" in findings[0].message.lower()


def test_never_indexed_auto_schedule_warns(tmp_path):
    """Auto-scheduled path with no last_indexed → warn."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="every 6 hours",
        expected_interval_seconds=6 * HOUR,
        last_indexed=None,
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "never"
    assert findings[0].severity == "warn"


def test_never_indexed_manual_schedule_passes(tmp_path):
    """Manual path that hasn't been rebuilt yet → pass (not a warn)."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="manual",
        last_indexed=None,
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "never"
    assert findings[0].severity == "pass"


# ---------------------------------------------------------------------------
# Manual schedule edge cases
# ---------------------------------------------------------------------------


def test_manual_schedule_old_index_does_not_warn(tmp_path):
    """Manual schedule + 30-day-old index → still pass (constraint §)."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="manual",
        expected_interval_seconds=None,  # manual has no interval anyway
        last_indexed=_iso(now - timedelta(days=30)),
        last_index_status="ok",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "ok"
    assert findings[0].severity == "pass"


def test_manual_schedule_corrupt_metadata_warns(tmp_path):
    """Manual schedule does NOT shield corrupt metadata — still warns."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="manual",
        last_indexed="not-a-real-timestamp",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "corrupt"
    assert findings[0].severity == "warn"


def test_manual_schedule_failed_status_warns(tmp_path):
    """Manual schedule + last_index_status=failed → warn (failure is signal)."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    cfg = _make_cfg(
        tmp_path,
        schedule="manual",
        last_indexed=_iso(now - timedelta(minutes=5)),
        last_index_status="failed",
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    assert findings[0].status == "failed"
    assert findings[0].severity == "warn"


# ---------------------------------------------------------------------------
# Corrupt config / loader robustness
# ---------------------------------------------------------------------------


def test_corrupt_yaml_returns_single_error_not_exception(tmp_path):
    """Garbage YAML → load_and_check returns ([], error_string), no raise."""
    bad = tmp_path / "vault.yaml"
    bad.write_text("version: 1\npaths: [{this is: \nbroken")
    findings, err = doctor_check.load_and_check(bad)
    assert findings == []
    assert err is not None
    assert "vault.yaml" in err


def test_unsupported_schema_version_returns_error(tmp_path):
    """Future schema version → load_and_check captures, no raise."""
    bad = tmp_path / "vault.yaml"
    bad.write_text("version: 99\npaths: []\n")
    findings, err = doctor_check.load_and_check(bad)
    assert findings == []
    assert err is not None
    assert "99" in err or "schema" in err.lower()


def test_missing_config_file_returns_empty_findings(tmp_path):
    """Absent vault.yaml → no findings, no error (consistent with VDS-01)."""
    findings, err = doctor_check.load_and_check(tmp_path / "absent.yaml")
    assert findings == []
    assert err is None


def test_empty_paths_list_returns_empty_findings(tmp_path):
    """Config with paths=[] → empty findings list (caller renders 'no paths')."""
    cfg_path = tmp_path / "vault.yaml"
    vault_config.save(vault_config.VaultConfig(), cfg_path)
    findings, err = doctor_check.load_and_check(cfg_path)
    assert findings == []
    assert err is None


# ---------------------------------------------------------------------------
# Multi-path aggregation
# ---------------------------------------------------------------------------


def test_summarize_counts_each_status(tmp_path):
    """summarize() returns a count-per-status dict."""
    now = datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc)
    fresh = tmp_path / "fresh"
    stale = tmp_path / "stale"
    fresh.mkdir()
    stale.mkdir()
    cfg = vault_config.VaultConfig(
        paths=[
            vault_config.VaultPathEntry(
                path=str(fresh),
                schedule="every 6 hours",
                expected_interval_seconds=6 * HOUR,
                last_indexed=_iso(now - timedelta(hours=1)),
                last_index_status="ok",
            ),
            vault_config.VaultPathEntry(
                path=str(stale),
                schedule="every 6 hours",
                expected_interval_seconds=6 * HOUR,
                last_indexed=_iso(now - timedelta(hours=20)),
                last_index_status="ok",
            ),
            vault_config.VaultPathEntry(
                path=str(tmp_path / "gone"),
                schedule="every 1h",
                expected_interval_seconds=HOUR,
                last_indexed=_iso(now - timedelta(minutes=1)),
                last_index_status="ok",
            ),
        ]
    )
    findings = doctor_check.check_vault_paths(cfg, now=now)
    summary = doctor_check.summarize(findings)
    assert summary["ok"] == 1
    assert summary["stale"] == 1
    assert summary["missing"] == 1


# ---------------------------------------------------------------------------
# Doctor CLI integration (end-to-end through run_doctor JSON output)
# ---------------------------------------------------------------------------


def test_run_doctor_json_includes_vds03_warning_for_stale_path(tmp_path, monkeypatch, capsys):
    """run_doctor() picks up the stale finding and emits it in --json output."""
    proj = tmp_path / "proj"
    proj.mkdir()

    cfg_file = tmp_path / "vault.yaml"
    now = datetime.now(timezone.utc)
    cfg = vault_config.VaultConfig(
        paths=[
            vault_config.VaultPathEntry(
                path=str(proj),
                schedule="every 6 hours",
                expected_interval_seconds=6 * HOUR,
                last_indexed=_iso(now - timedelta(hours=24)),
                last_index_status="ok",
            )
        ]
    )
    vault_config.save(cfg, cfg_file)

    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_file))
    # Avoid touching the real ~/.tokenpak.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    from tokenpak.cli.commands import doctor as doctor_mod

    rc = doctor_mod.run_doctor(output_json=True)

    captured = capsys.readouterr().out
    # The JSON object is the last well-formed thing on stdout. Strip prefix prints.
    last_brace = captured.rfind("\n{")
    json_text = captured[last_brace + 1 :] if last_brace != -1 else captured
    payload = json.loads(json_text)

    # At least one VDS-03 path-status check + one summary is recorded.
    vds03_checks = [
        c for c in payload["checks"] if c["check"].startswith("vault_path")
    ]
    assert vds03_checks, "VDS-03 checks should be in doctor output"
    assert any(c["status"] == "warn" for c in vds03_checks), (
        f"expected at least one warn for stale path, got {vds03_checks}"
    )
    # Exit code is 1 (warnings) or 2 (errors); never 0 because we forced a stale.
    assert rc in (1, 2)


def test_run_doctor_json_no_paths_registered_does_not_warn(tmp_path, monkeypatch, capsys):
    """An empty vault.yaml does not introduce a stale-paths warning."""
    cfg_file = tmp_path / "vault.yaml"
    vault_config.save(vault_config.VaultConfig(), cfg_file)
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_file))
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    from tokenpak.cli.commands import doctor as doctor_mod

    doctor_mod.run_doctor(output_json=True)
    captured = capsys.readouterr().out
    last_brace = captured.rfind("\n{")
    payload = json.loads(captured[last_brace + 1 :] if last_brace != -1 else captured)

    vault_paths_checks = [
        c for c in payload["checks"] if c["check"] == "vault_paths_staleness"
    ]
    # Single 'no registered paths' pass record.
    assert len(vault_paths_checks) == 1
    assert vault_paths_checks[0]["status"] == "pass"


def test_run_doctor_corrupt_yaml_warns_but_does_not_fail_other_checks(
    tmp_path, monkeypatch, capsys
):
    """Per spec constraint: doctor must not fail unrelated checks on bad config."""
    cfg_file = tmp_path / "vault.yaml"
    cfg_file.write_text("version: 1\npaths: [{not real:\nbroken")
    monkeypatch.setenv("TOKENPAK_VAULT_CONFIG", str(cfg_file))
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    from tokenpak.cli.commands import doctor as doctor_mod

    rc = doctor_mod.run_doctor(output_json=True)
    captured = capsys.readouterr().out
    last_brace = captured.rfind("\n{")
    payload = json.loads(captured[last_brace + 1 :] if last_brace != -1 else captured)

    # The VDS-03 entry should be a warn, not a fail.
    vds03 = [c for c in payload["checks"] if c["check"] == "vault_paths_staleness"]
    assert len(vds03) == 1
    assert vds03[0]["status"] == "warn"
    # rc is 1 (warn) or 2 (errors) — corrupt config alone is a warn.
    assert rc in (1, 2)
