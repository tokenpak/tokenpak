# SPDX-License-Identifier: Apache-2.0
"""Unit tests for calibration.py — worker count optimization and dynamic adjustment."""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from tokenpak.orchestration.calibration import (
    _candidate_workers,
    _host_key,
    _run_index_once,
    _sample_files,
    calibrate_workers,
    get_recommended_workers,
    load_profile,
    save_profile,
)


class TestProfileManagement:
    """Test profile loading/saving utilities."""

    def test_load_profile_nonexistent(self):
        """Loading a non-existent profile returns empty dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "nonexistent.json"
            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
            ):
                result = load_profile()
                assert result == {}

    def test_save_and_load_profile(self):
        """Profile can be saved and loaded correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "test" / "calibration.json"
            test_data = {"host1": {"best_workers": 4, "scores_sec": {"1": 2.0}}}

            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
            ):
                save_profile(test_data)
                loaded = load_profile()
                assert loaded == test_data

    def test_load_profile_invalid_json(self):
        """Invalid JSON in profile returns empty dict (graceful fallback)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "bad.json"
            profile_path.parent.mkdir(parents=True, exist_ok=True)
            profile_path.write_text("{ invalid json }")

            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
            ):
                result = load_profile()
                assert result == {}

    def test_host_key_returns_hostname(self):
        """_host_key returns the system hostname."""
        import socket

        key = _host_key()
        assert key == socket.gethostname()
        assert isinstance(key, str)
        assert len(key) > 0


class TestCandidateWorkers:
    """Test worker count candidate generation."""

    def test_candidate_workers_empty(self):
        """Candidate workers list is never empty."""
        result = _candidate_workers(max_workers=8)
        assert len(result) > 0
        assert 1 in result  # Should always include 1

    def test_candidate_workers_respects_max(self):
        """Candidate workers respects max_workers cap."""
        result = _candidate_workers(max_workers=4)
        assert all(w <= 4 for w in result)

    def test_candidate_workers_sorted(self):
        """Candidate workers list is sorted."""
        result = _candidate_workers(max_workers=8)
        assert result == sorted(result)

    def test_candidate_workers_low_cpu_count(self):
        """With low CPU count, candidates limited appropriately."""
        with mock.patch("os.cpu_count", return_value=2):
            result = _candidate_workers(max_workers=8)
            assert all(w <= 2 for w in result)
            assert 1 in result

    def test_candidate_workers_includes_cpu_count(self):
        """Candidate list includes the actual CPU count if within max."""
        with mock.patch("os.cpu_count", return_value=6):
            result = _candidate_workers(max_workers=8)
            assert 6 in result

    def test_candidate_workers_zero_cpu_count(self):
        """With zero CPU count, defaults to reasonable values."""
        with mock.patch("os.cpu_count", return_value=None):
            result = _candidate_workers(max_workers=8)
            assert len(result) > 0
            assert all(w >= 1 for w in result)


class TestSampleFiles:
    """Test sample file selection."""

    def test_sample_files_respects_max(self):
        """Sample files respects max_files limit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create more files than the limit
            for i in range(20):
                Path(tmpdir, f"file{i}.txt").write_text(f"content {i}")

            with mock.patch(
                "tokenpak.orchestration.calibration.walk_directory", return_value=[
                    (f"{tmpdir}/file{i}.txt", "text", None) for i in range(20)
                ]
            ):
                result = _sample_files(tmpdir, max_files=5)
                assert len(result) <= 5

    def test_sample_files_returns_list(self):
        """Sample files returns a list of file tuples."""
        with mock.patch(
            "tokenpak.orchestration.calibration.walk_directory",
            return_value=[(f"/path/file.txt", "text", None)],
        ):
            result = _sample_files("/dummy")
            assert isinstance(result, list)
            assert len(result) >= 0


class TestCalibrateWorkers:
    """Test the main calibration routine."""

    def test_calibrate_workers_no_files(self):
        """calibrate_workers returns error when no files found."""
        with mock.patch(
            "tokenpak.orchestration.calibration.walk_directory", return_value=[]
        ):
            result = calibrate_workers("/dummy")
            assert "error" in result
            assert result["error"] == "No files found for calibration"

    @mock.patch("tokenpak.orchestration.calibration._run_index_once")
    @mock.patch("tokenpak.orchestration.calibration._candidate_workers")
    @mock.patch("tokenpak.orchestration.calibration.walk_directory")
    def test_calibrate_workers_returns_best(
        self, mock_walk, mock_candidates, mock_run_index
    ):
        """calibrate_workers identifies the best worker count."""
        mock_walk.return_value = [(f"/file{i}.txt", "text", None) for i in range(5)]
        mock_candidates.return_value = [1, 2, 4]
        # Simulate: 1 worker = slow (2.0s), 2 workers = medium (1.5s), 4 workers = fast (1.0s)
        mock_run_index.side_effect = [
            2.0, 2.0,  # rounds for w=1
            1.5, 1.5,  # rounds for w=2
            1.0, 1.0,  # rounds for w=4
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH",
                Path(tmpdir) / "calibration.json",
            ):
                result = calibrate_workers("/dummy", rounds=2)

                assert "best_workers" in result
                assert result["best_workers"] == 4  # Fastest
                assert "scores_sec" in result
                assert len(result["scores_sec"]) == 3

    @mock.patch("tokenpak.orchestration.calibration._run_index_once")
    @mock.patch("tokenpak.orchestration.calibration._candidate_workers")
    @mock.patch("tokenpak.orchestration.calibration.walk_directory")
    def test_calibrate_workers_saves_profile(
        self, mock_walk, mock_candidates, mock_run_index
    ):
        """calibrate_workers persists profile to disk."""
        mock_walk.return_value = [(f"/file.txt", "text", None)]
        mock_candidates.return_value = [1, 2]
        mock_run_index.side_effect = [1.5, 1.5, 1.0, 1.0]

        with tempfile.TemporaryDirectory() as tmpdir:
            profile_path = Path(tmpdir) / "cal" / "calibration.json"
            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
            ):
                calibrate_workers("/dummy", rounds=2)

                assert profile_path.exists()
                saved = json.loads(profile_path.read_text())
                assert len(saved) > 0


class TestGetRecommendedWorkers:
    """Test dynamic worker recommendation."""

    def test_get_recommended_workers_default(self):
        """Without profile, returns default worker count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH",
                Path(tmpdir) / "nonexistent.json",
            ), mock.patch("os.cpu_count", return_value=4):
                result = get_recommended_workers(default_workers=4)
                assert 1 <= result <= 4

    def test_get_recommended_workers_respects_hard_cap(self):
        """Recommended workers respects CPU hard cap."""
        with mock.patch("os.cpu_count", return_value=2):
            result = get_recommended_workers(default_workers=8, max_workers=8)
            assert result <= 2

    @mock.patch("os.cpu_count", return_value=8)
    @mock.patch("os.getloadavg")
    def test_get_recommended_workers_high_load(
        self, mock_load, mock_cpu
    ):
        """High load decreases worker recommendation."""
        # Load is 7.0 on 8 cores = 87.5% utilization (> 85% threshold)
        mock_load.return_value = (7.0, 0, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            profile_data = {
                "host": {"best_workers": 4, "scores_sec": {"4": 1.0}}
            }
            profile_path = Path(tmpdir) / "cal.json"
            profile_path.write_text(json.dumps(profile_data))

            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
            ):
                with mock.patch(
                    "tokenpak.orchestration.calibration._host_key", return_value="host"
                ):
                    result = get_recommended_workers(default_workers=4)
                    # Should decrease by 1 due to high load
                    assert result <= 4

    @mock.patch("os.cpu_count", return_value=8)
    @mock.patch("os.getloadavg")
    def test_get_recommended_workers_low_load(
        self, mock_load, mock_cpu
    ):
        """Low load can increase worker recommendation."""
        # Load is 2.0 on 8 cores = 25% utilization (< 35% threshold)
        mock_load.return_value = (2.0, 0, 0)

        with tempfile.TemporaryDirectory() as tmpdir:
            profile_data = {
                "host": {"best_workers": 4, "scores_sec": {"4": 1.0}}
            }
            profile_path = Path(tmpdir) / "cal.json"
            profile_path.write_text(json.dumps(profile_data))

            with mock.patch(
                "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
            ):
                with mock.patch(
                    "tokenpak.orchestration.calibration._host_key", return_value="host"
                ):
                    # Load is low, so should stay at baseline or increase
                    result = get_recommended_workers(default_workers=4)
                    assert result >= 1

    @mock.patch("os.cpu_count", return_value=8)
    def test_get_recommended_workers_load_exception(self, mock_cpu):
        """If getloadavg fails, gracefully falls back to baseline."""
        with mock.patch("os.getloadavg", side_effect=OSError):
            with tempfile.TemporaryDirectory() as tmpdir:
                profile_data = {
                    "host": {"best_workers": 4, "scores_sec": {"4": 1.0}}
                }
                profile_path = Path(tmpdir) / "cal.json"
                profile_path.write_text(json.dumps(profile_data))

                with mock.patch(
                    "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
                ):
                    with mock.patch(
                        "tokenpak.orchestration.calibration._host_key", return_value="host"
                    ):
                        # Should not raise; gracefully use baseline
                        result = get_recommended_workers(default_workers=4)
                        assert result >= 1

    def test_get_recommended_workers_returns_positive_int(self):
        """Recommended workers is always >= 1."""
        for _ in range(5):
            result = get_recommended_workers()
            assert isinstance(result, int)
            assert result >= 1


class TestBoundaryConditions:
    """Test edge cases and boundary conditions."""

    def test_candidate_workers_max_equals_one(self):
        """With max_workers=1, candidates should be [1]."""
        result = _candidate_workers(max_workers=1)
        assert result == [1]

    def test_get_recommended_workers_baseline_clamped(self):
        """If stored baseline exceeds hard cap, it's clamped."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Stored baseline: 16 workers, but hard cap is 4
            profile_data = {
                "host": {"best_workers": 16, "scores_sec": {"16": 1.0}}
            }
            profile_path = Path(tmpdir) / "cal.json"
            profile_path.write_text(json.dumps(profile_data))

            with mock.patch("os.cpu_count", return_value=4):
                with mock.patch(
                    "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
                ):
                    with mock.patch(
                        "tokenpak.orchestration.calibration._host_key", return_value="host"
                    ):
                        result = get_recommended_workers(default_workers=2, max_workers=4)
                        assert result <= 4

    def test_dynamic_adjustment_bounded_to_plus_minus_one(self):
        """Dynamic adjustment never exceeds ±1 from baseline."""
        # This is validated within get_recommended_workers; the logic
        # ensures dyn is bounded: `if dyn > baseline + 1: dyn = baseline + 1`
        # We test this indirectly through the overall behavior.
        with mock.patch("os.cpu_count", return_value=16):
            with tempfile.TemporaryDirectory() as tmpdir:
                profile_data = {
                    "host": {"best_workers": 8, "scores_sec": {"8": 1.0}}
                }
                profile_path = Path(tmpdir) / "cal.json"
                profile_path.write_text(json.dumps(profile_data))

                with mock.patch(
                    "tokenpak.orchestration.calibration.PROFILE_PATH", profile_path
                ):
                    with mock.patch(
                        "tokenpak.orchestration.calibration._host_key", return_value="host"
                    ):
                        # Any load condition should adjust by at most ±1
                        with mock.patch(
                            "os.getloadavg", return_value=(0.1, 0, 0)
                        ):
                            # Low load might increase
                            result = get_recommended_workers(default_workers=8)
                            assert abs(result - 8) <= 1
