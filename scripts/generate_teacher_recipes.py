#!/usr/bin/env python3
"""Generate deterministic teacher-pack context recipes."""

from __future__ import annotations

from tokenpak.agent.cli.commands.teacher import run_teacher_cmd


if __name__ == "__main__":
    run_teacher_cmd(["generate"])
