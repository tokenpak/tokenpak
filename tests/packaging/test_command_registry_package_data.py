# SPDX-License-Identifier: Apache-2.0
"""Packaging regressions for the CLI command registry."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_command_registry_declared_as_package_data() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["tokenpak"]

    assert "core/registry/*.json" in package_data


def test_command_registry_loads_as_package_resource() -> None:
    registry = resources.files("tokenpak.core.registry").joinpath("commands.json")

    with registry.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    commands = payload.get("commands", [])
    command_names = {entry.get("command") for entry in commands}
    assert commands
    assert {"start", "status", "doctor"} <= command_names
