# SPDX-License-Identifier: Apache-2.0
"""Execute one arm of a prove run.

This module re-exports the core types and provides a backwards-compatible
``run_arm()`` wrapper.  All execution logic lives in ``adapter.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .adapter import ArmConfig, ArmResult, TurnResult  # noqa: F401 — re-export
from .adapter import run_arm as _adapter_run_arm
from .scenario import Scenario


def run_arm(
    scenario: Scenario,
    proxied: bool = False,
    log_path: Optional[Path] = None,
    on_turn_complete: Optional[callable] = None,
    *,
    arm_config: Optional[ArmConfig] = None,
) -> ArmResult:
    """Run all turns of a scenario through one arm.

    Supports two calling conventions:

    **Legacy** (two-arm direct vs proxied)::

        run_arm(scenario, proxied=True)

    **New** (any adapter combo via ArmConfig)::

        run_arm(scenario, arm_config=ArmConfig(
            name="Grok Direct",
            platform="api",
            provider="xai",
            model="grok-3",
        ))
    """
    if arm_config is None:
        arm_config = ArmConfig(
            name="tokenpak" if proxied else "direct",
            platform="proxy" if proxied else "api",
            provider=scenario.provider,
            model=scenario.model,
            via_tokenpak=proxied,
        )

    return _adapter_run_arm(
        cfg=arm_config,
        turns=scenario.turns,
        system=scenario.system,
        max_tokens=scenario.max_tokens,
        log_path=log_path,
        on_turn_complete=on_turn_complete,
    )
