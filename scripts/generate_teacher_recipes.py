#!/usr/bin/env python3
"""Generate deterministic teacher-pack context recipes."""

from __future__ import annotations

from tokenpak.compression.teacher.builder import build_teacher_pack


if __name__ == "__main__":
    result = build_teacher_pack(
        source_roots=["~/.tokenpak/vault"],
        command_roots=["~/.tokenpak/commands"],
        output_root="~/.tokenpak/teacher-output",
    )
    print(f"Generated {result.recipe_count} recipes -> {result.output_dir}")
