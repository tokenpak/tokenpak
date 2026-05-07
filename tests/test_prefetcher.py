
import pytest

pytest.importorskip("tokenpak.agentic.prefetcher", reason="module not available in current build")
from tokenpak.agentic.prefetcher import PredictivePrefetcher


def test_transition_learning_recommends_highest_frequency_artifacts():
    p = PredictivePrefetcher()
    p.record_transition("step-a", "step-b", ["A", "B"])
    p.record_transition("step-a", "step-c", ["A", "C"])
    p.record_transition("step-a", "step-b", ["A"])

    assert p.recommend_for_completed_step("step-a", limit=3) == ["A", "B", "C"]


def test_workflow_step_completed_triggers_preload_calls():
    p = PredictivePrefetcher()
    p.record_transition("compile", "test", ["pytest.ini", "tests/test_api.py"])

    loaded = []
    returned = p.on_workflow_step_completed("compile", preload=loaded.append)

    assert returned == ["pytest.ini", "tests/test_api.py"]
    assert loaded == ["pytest.ini", "tests/test_api.py"]


def test_task_type_recognized_prefetches_common_files():
    p = PredictivePrefetcher()
    p.register_task_type_artifacts("bugfix", ["src/service.py", "tests/test_service.py"])
    p.register_task_type_artifacts("bugfix", ["src/service.py"])

    loaded = []
    result = p.on_task_type_recognized("bugfix", preload=loaded.append)

    assert result == ["src/service.py", "tests/test_service.py"]
    assert loaded == result


def test_error_detected_prefetches_diagnostics_and_dedupes_extra():
    p = PredictivePrefetcher()
    p.register_error_artifacts("timeout", ["logs/http.log", "config/retries.yaml"])

    loaded = []
    result = p.on_error_detected(
        "timeout",
        preload=loaded.append,
        extra_artifacts=["logs/http.log", "tmp/trace.txt"],
    )

    assert "logs/latest.log" in result
    assert "config/settings.yaml" in result
    assert "env/runtime.env" in result
    assert result.count("logs/http.log") == 1
    assert "tmp/trace.txt" in result
    assert loaded == result
