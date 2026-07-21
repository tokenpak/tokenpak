# SPDX-License-Identifier: Apache-2.0
"""CLI entrypoint smoke for ``tokenpak dashboard --json``."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def test_dashboard_json_cli_emits_v2_contract(tmp_path) -> None:
    env = os.environ.copy()
    env["TOKENPAK_HOME"] = str(tmp_path)
    proc = subprocess.run(
        [sys.executable, "-m", "tokenpak.cli.main", "dashboard", "--json"],
        capture_output=True,
        env=env,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["schema_version"] == "dashboard.v2.0"
    assert payload["summary"]["proxy"]["state"] in {"running", "degraded", "unknown"}
    assert payload["spend"]["saved_usd"]["state"] in {"measured", "not_measured"}
    assert payload["layout"]["name"] == "home"


def test_dashboard_layout_json_cli_emits_readonly_layouts(tmp_path) -> None:
    env = os.environ.copy()
    env["TOKENPAK_HOME"] = str(tmp_path)

    for layout in ("home", "dispatch", "spend", "debug"):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tokenpak.cli.main",
                "dashboard",
                "--layout",
                layout,
                "--json",
            ],
            capture_output=True,
            env=env,
            text=True,
            timeout=10,
        )

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout)
        assert payload["layout"]["name"] == layout
        assert payload["layout"]["read_only"] is True
        assert payload["layout"]["mutation_controls"] == []
