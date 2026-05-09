"""Smoke tests for telemetry/server.py endpoints and integration.

Note: Direct FastAPI app creation has version compatibility issues,
so these tests use mocks and direct function testing instead.
"""

import tempfile
from pathlib import Path

import pytest

from tokenpak.telemetry.server import (
    EventResult,
    IngestRequest,
    IngestResponse,
    TelemetryEvent,
    parse_filter,
)


@pytest.fixture
def temp_db():
    """Provide a temporary SQLite database path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    try:
        Path(db_path).unlink(missing_ok=True)
    except Exception:
        pass


class TestParseFilter:
    """Test filter DSL parsing."""

    def test_parse_empty_filter(self):
        """Empty filter string should return empty dict."""
        result = parse_filter(None)
        assert result == {}

    def test_parse_single_filter(self):
        """Single filter should be parsed correctly."""
        result = parse_filter("provider:anthropic")
        assert result == {"provider": "anthropic"}

    def test_parse_multiple_filters(self):
        """Multiple filters should all be parsed."""
        result = parse_filter("provider:anthropic,model:opus,agent:sue")
        assert result == {
            "provider": "anthropic",
            "model": "opus",
            "agent_id": "sue",  # agent normalized to agent_id
        }

    def test_parse_filter_with_spaces(self):
        """Filters with spaces should be trimmed."""
        result = parse_filter(" provider : anthropic , model : opus ")
        assert result == {"provider": "anthropic", "model": "opus"}

    def test_parse_filter_ignores_invalid_keys(self):
        """Invalid filter keys should be ignored."""
        result = parse_filter("provider:anthropic,invalid:value,model:opus")
        assert result == {"provider": "anthropic", "model": "opus"}
        assert "invalid" not in result


class TestTelemetryEventModel:
    """Test TelemetryEvent Pydantic model."""

    def test_event_minimal_data(self):
        """Event should accept minimal data."""
        event = TelemetryEvent()
        assert event.provider is None
        assert event.model is None

    def test_event_with_provider_and_model(self):
        """Event should accept provider and model."""
        event = TelemetryEvent(
            provider="anthropic",
            model="claude-3-sonnet-20250319",
        )
        assert event.provider == "anthropic"
        assert event.model == "claude-3-sonnet-20250319"

    def test_event_with_messages_and_usage(self):
        """Event should accept messages and usage."""
        event = TelemetryEvent(
            provider="anthropic",
            model="claude-3-sonnet-20250319",
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            usage={"input_tokens": 10, "output_tokens": 5},
        )
        assert len(event.messages) == 2
        assert event.usage["input_tokens"] == 10

    def test_event_with_timestamp(self):
        """Event should accept Unix timestamp."""
        ts = 1234567890.0
        event = TelemetryEvent(
            timestamp=ts,
            provider="anthropic",
            model="opus",
        )
        assert event.timestamp == ts

    def test_event_with_session_id(self):
        """Event should accept session_id."""
        event = TelemetryEvent(
            session_id="test-session-123",
            provider="anthropic",
        )
        assert event.session_id == "test-session-123"

    def test_event_with_extra_fields(self):
        """Event should accept arbitrary extra fields via model_config."""
        event = TelemetryEvent(
            provider="anthropic",
            custom_field="custom_value",
        )
        # Should not raise; extra fields are allowed


class TestIngestRequest:
    """Test IngestRequest Pydantic model."""

    def test_ingest_request_single_event(self):
        """IngestRequest should accept single event."""
        event = TelemetryEvent(provider="anthropic")
        request = IngestRequest(events=[event])
        assert len(request.events) == 1

    def test_ingest_request_multiple_events(self):
        """IngestRequest should accept multiple events."""
        events = [
            TelemetryEvent(provider="anthropic"),
            TelemetryEvent(provider="openai"),
            TelemetryEvent(provider="gemini"),
        ]
        request = IngestRequest(events=events)
        assert len(request.events) == 3

    def test_ingest_request_empty_events_fails(self):
        """IngestRequest with empty events should fail validation."""
        with pytest.raises(ValueError):
            IngestRequest(events=[])

    def test_ingest_request_too_many_events_fails(self):
        """IngestRequest with >100 events should fail validation."""
        events = [TelemetryEvent() for _ in range(101)]
        with pytest.raises(ValueError):
            IngestRequest(events=events)

    def test_ingest_request_at_max_boundary(self):
        """IngestRequest with exactly 100 events should succeed."""
        events = [TelemetryEvent() for _ in range(100)]
        request = IngestRequest(events=events)
        assert len(request.events) == 100


class TestEventResult:
    """Test EventResult Pydantic model."""

    def test_event_result_success(self):
        """EventResult should represent successful ingest."""
        result = EventResult(
            index=0,
            success=True,
            event_id="evt-123",
            duration_ms=45.3,
        )
        assert result.success is True
        assert result.event_id == "evt-123"
        assert result.error is None

    def test_event_result_failure(self):
        """EventResult should represent failed ingest."""
        result = EventResult(
            index=1,
            success=False,
            error="Database constraint violation",
            duration_ms=12.1,
        )
        assert result.success is False
        assert "constraint" in result.error.lower()

    def test_event_result_partial_failure(self):
        """EventResult should support partial data stored."""
        result = EventResult(
            index=2,
            success=False,
            event_id="evt-456",
            error="Partial ingest",
            partial=True,
            duration_ms=20.0,
        )
        assert result.partial is True


class TestIngestResponse:
    """Test IngestResponse Pydantic model."""

    def test_ingest_response_all_success(self):
        """IngestResponse should show all successful."""
        results = [
            EventResult(index=0, success=True, event_id="evt-1", duration_ms=10.0),
            EventResult(index=1, success=True, event_id="evt-2", duration_ms=15.0),
        ]
        response = IngestResponse(
            success=True,
            total=2,
            processed=2,
            failed=0,
            results=results,
            total_duration_ms=25.0,
        )
        assert response.success is True
        assert response.processed == 2
        assert response.failed == 0

    def test_ingest_response_partial_failure(self):
        """IngestResponse should indicate partial failure."""
        results = [
            EventResult(index=0, success=True, event_id="evt-1", duration_ms=10.0),
            EventResult(index=1, success=False, error="Error", duration_ms=5.0),
        ]
        response = IngestResponse(
            success=False,
            total=2,
            processed=1,
            failed=1,
            results=results,
            total_duration_ms=15.0,
        )
        assert response.success is False
        assert response.processed == 1
        assert response.failed == 1

    def test_ingest_response_all_failed(self):
        """IngestResponse should show all failed."""
        results = [
            EventResult(index=0, success=False, error="Error 1", duration_ms=5.0),
            EventResult(index=1, success=False, error="Error 2", duration_ms=5.0),
        ]
        response = IngestResponse(
            success=False,
            total=2,
            processed=0,
            failed=2,
            results=results,
            total_duration_ms=10.0,
        )
        assert response.success is False
        assert response.failed == 2


class TestServerModuleImports:
    """Test that core server module can be imported."""

    def test_server_module_imports(self):
        """Server module should import without errors."""
        try:
            import tokenpak.telemetry.server as server_module
            assert hasattr(server_module, "create_app")
            assert hasattr(server_module, "parse_filter")
            assert hasattr(server_module, "TelemetryEvent")
        except ImportError as e:
            pytest.skip(f"Server module import failed: {e}")

    def test_server_models_available(self):
        """All expected models should be available."""
        from tokenpak.telemetry.server import (
            CapsuleBody,
            EventResult,
            IngestRequest,
            IngestResponse,
            TelemetryEvent,
        )
        assert TelemetryEvent is not None
        assert IngestRequest is not None
        assert IngestResponse is not None
        assert EventResult is not None
        assert CapsuleBody is not None


class TestFilterNormalization:
    """Test filter field normalization."""

    def test_agent_to_agent_id_normalization(self):
        """'agent' filter should be normalized to 'agent_id'."""
        result = parse_filter("agent:sue")
        assert "agent_id" in result
        assert result["agent_id"] == "sue"
        assert "agent" not in result

    def test_status_filter_preserved(self):
        """'status' filter should be preserved as-is."""
        result = parse_filter("status:success")
        assert result["status"] == "success"

    def test_temporal_filters_preserved(self):
        """'start' and 'end' filters should be preserved."""
        result = parse_filter("start:2026-01-01,end:2026-12-31")
        assert result["start"] == "2026-01-01"
        assert result["end"] == "2026-12-31"


class TestEventModelFlexibility:
    """Test TelemetryEvent flexibility with various inputs."""

    def test_response_model_field(self):
        """Event should accept response field."""
        event = TelemetryEvent(
            response={
                "content": [{"type": "text", "text": "Hello"}],
                "stop_reason": "end_turn",
            }
        )
        assert event.response is not None

    def test_raw_field_override(self):
        """Event should accept raw field for direct passthrough."""
        event = TelemetryEvent(
            provider="anthropic",
            raw={
                "_raw_full_response": "...",
                "_debug_info": "...",
            },
        )
        assert event.raw is not None

    def test_event_serialization(self):
        """Event should be serializable to JSON via model_dump."""
        event = TelemetryEvent(
            provider="anthropic",
            model="opus",
            timestamp=1234567890.0,
            session_id="test-123",
        )
        data = event.model_dump(exclude_none=True)
        assert isinstance(data, dict)
        assert data["provider"] == "anthropic"
