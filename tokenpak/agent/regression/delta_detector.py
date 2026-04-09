# SPDX-License-Identifier: Apache-2.0
"""Delta detector for TokenPak regression detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class DeltaResult:
    """Result of delta measurement."""

    is_trivial: bool
    changed_dimensions: List[str]
    magnitude: float
    details: Dict[str, Any]

    @property
    def is_moderate(self) -> bool:
        """Check if delta is moderate (not trivial, not large)."""
        return not self.is_trivial and self.magnitude < 0.6

    @property
    def is_large(self) -> bool:
        """Check if delta is large."""
        return self.magnitude >= 0.6


class DeltaDetector:
    """Detect meaningful changes between states."""

    def __init__(
        self,
        max_trivial_lines: int = 15,
        max_trivial_files: int = 2,
        trivial_magnitude_threshold: float = 0.15,
    ):
        """
        Initialize delta detector.

        Args:
            max_trivial_lines: Max lines changed for trivial delta
            max_trivial_files: Max files changed for trivial delta
            trivial_magnitude_threshold: Magnitude threshold for trivial (0-1)
        """
        self.max_trivial_lines = max_trivial_lines
        self.max_trivial_files = max_trivial_files
        self.trivial_magnitude_threshold = trivial_magnitude_threshold

    def compute_delta(
        self,
        current_state: Dict[str, Any],
        baseline_state: Dict[str, Any],
    ) -> DeltaResult:
        """
        Compute delta between current and baseline state.

        Args:
            current_state: Current input/config state
            baseline_state: Last known good state

        Returns:
            DeltaResult with is_trivial, changed_dimensions, magnitude
        """
        changed_dimensions = []
        details = {}

        # Compare lines changed
        current_lines = current_state.get("lines", 0)
        baseline_lines = baseline_state.get("lines", 0)
        lines_diff = abs(current_lines - baseline_lines)

        if lines_diff > 0:
            changed_dimensions.append("lines")
            details["lines_diff"] = lines_diff

        # Compare files changed
        current_files = set(current_state.get("files", []))
        baseline_files = set(baseline_state.get("files", []))
        files_diff = len(current_files.symmetric_difference(baseline_files))

        if files_diff > 0:
            changed_dimensions.append("files")
            details["files_diff"] = files_diff

        # Compare dependencies
        current_deps = set(current_state.get("dependencies", []))
        baseline_deps = set(baseline_state.get("dependencies", []))
        deps_diff = len(current_deps.symmetric_difference(baseline_deps))

        if deps_diff > 0:
            changed_dimensions.append("dependencies")
            details["deps_diff"] = deps_diff

        # Compare config
        current_config = current_state.get("config", {})
        baseline_config = baseline_state.get("config", {})
        config_keys_diff = set(current_config.keys()).symmetric_difference(baseline_config.keys())

        if config_keys_diff:
            changed_dimensions.append("config")
            details["config_diff"] = len(config_keys_diff)

        # Compute magnitude (0-1 scale)
        magnitude = self._compute_magnitude(
            lines_diff, files_diff, deps_diff, len(config_keys_diff)
        )

        # Determine if trivial
        is_trivial = (
            lines_diff <= self.max_trivial_lines
            and files_diff <= self.max_trivial_files
            and magnitude <= self.trivial_magnitude_threshold
        )

        return DeltaResult(
            is_trivial=is_trivial,
            changed_dimensions=changed_dimensions,
            magnitude=magnitude,
            details=details,
        )

    def _compute_magnitude(
        self, lines_diff: int, files_diff: int, deps_diff: int, config_diff: int
    ) -> float:
        """
        Compute delta magnitude (0-1 scale).

        Args:
            lines_diff: Number of lines changed
            files_diff: Number of files changed
            deps_diff: Number of dependencies changed
            config_diff: Number of config keys changed

        Returns:
            Magnitude score (0.0-1.0)
        """
        # Weighted scoring
        line_score = min(lines_diff / 100.0, 1.0) * 0.3
        file_score = min(files_diff / 5.0, 1.0) * 0.3
        deps_score = min(deps_diff / 10.0, 1.0) * 0.2
        config_score = min(config_diff / 10.0, 1.0) * 0.2

        return line_score + file_score + deps_score + config_score

    def should_reuse_baseline(self, delta: DeltaResult, baseline_still_passes: bool) -> bool:
        """
        Decide whether to reuse baseline artifact.

        Args:
            delta: Delta measurement
            baseline_still_passes: Whether baseline validation still passes

        Returns:
            True if baseline should be reused
        """
        # Reuse only if trivial delta AND baseline still passes
        return delta.is_trivial and baseline_still_passes

    def should_validate_only(self, delta: DeltaResult) -> bool:
        """
        Decide whether to run validation-only (not full regen).

        Args:
            delta: Delta measurement

        Returns:
            True if validation-only is appropriate
        """
        # Validate for moderate deltas
        return delta.is_moderate

    def should_regenerate(self, delta: DeltaResult) -> bool:
        """
        Decide whether to regenerate (full recomputation).

        Args:
            delta: Delta measurement

        Returns:
            True if full regeneration needed
        """
        # Regenerate for large deltas
        return delta.is_large
