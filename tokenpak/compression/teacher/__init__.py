"""Teacher pack builder for deterministic context recipe generation."""

import warnings as _warnings

_warnings.warn(
    "tokenpak.compression.teacher — context recipe generation.This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .builder import TeacherPackBuilder, TeacherPackResult, build_teacher_pack

__all__ = ["TeacherPackBuilder", "TeacherPackResult", "build_teacher_pack", "builder"]
