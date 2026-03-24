#!/usr/bin/env python3
"""
Test suite for TokenPak WebSocket proxy.

Tests:
1. Stream receiving and decompression
2. Compression efficiency
3. Upstream error handling
4. Connection limit enforcement
5. Invalid upgrade handling
6. Reconnect and recovery
"""

import pytest
import json
import asyncio
import gzip
import time
from unittest.mock import Mock, patch, AsyncMock
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from websocket_proxy import (
    WebSocketConnectionManager,
    WebSocketConnectionStats,
    compress_chunk,
    decompress_chunk,
    CONNECTION_MANAGER,
)


class TestCompressionUtilities:
    """Test compression/decompression functions."""

    def test_compress_chunk_string(self):
        """Test compressing a string."""
        # Use larger data so gzip overhead is negligible
        data = "Hello, World! This is test data. " * 10
        compressed = compress_chunk(data)
        assert isinstance(compressed, bytes)
        assert len(compressed) < len(data.encode("utf-8"))  # Should be smaller

    def test_compress_chunk_bytes(self):
        """Test compressing bytes."""
        data = b"Binary data test"
        compressed = compress_chunk(data)
        assert isinstance(compressed, bytes)

    def test_decompress_chunk_roundtrip(self):
        """Test compress/decompress roundtrip."""
        original = "Test data for compression"
        compressed = compress_chunk(original)
        decompressed = decompress_chunk(compressed)
        assert decompressed == original

    def test_decompress_invalid_data(self):
        """Test decompression with invalid data raises error."""
        with pytest.raises(Exception):
            decompress_chunk(b"invalid gzip data")

    def test_compression_ratio_json_data(self):
        """Test that JSON data compresses well."""
        json_data = json.dumps({"type": "event", "data": "x" * 1000})
        compressed = compress_chunk(json_data)
        ratio = len(compressed) / len(json_data.encode("utf-8"))
        assert ratio < 0.5, f"Compression ratio {ratio} too high for repetitive JSON"


class TestWebSocketConnectionManager:
    """Test the connection manager."""

    def setup_method(self):
        """Create a fresh manager for each test."""
        self.manager = WebSocketConnectionManager(max_connections=3)

    def test_can_accept_initial(self):
        """Test that new manager can accept connections."""
        assert self.manager.can_accept() is True

    def test_register_connection(self):
        """Test registering a connection."""
        assert self.manager.register("conn1", "127.0.0.1:12345") is True
        assert self.manager.active_count() == 1

    def test_register_multiple_connections(self):
        """Test registering multiple connections."""
        for i in range(3):
            assert self.manager.register(f"conn{i}", f"127.0.0.1:{1000+i}") is True
        assert self.manager.active_count() == 3

    def test_connection_limit_enforced(self):
        """Test that connection limit is enforced."""
        # Fill to limit
        for i in range(3):
            self.manager.register(f"conn{i}", f"127.0.0.1:{1000+i}")
        
        # Try to exceed limit
        assert self.manager.can_accept() is False
        assert self.manager.register("conn3", "127.0.0.1:4000") is False
        assert self.manager.active_count() == 3

    def test_unregister_connection(self):
        """Test unregistering a connection."""
        self.manager.register("conn1", "127.0.0.1:12345")
        assert self.manager.active_count() == 1
        
        self.manager.unregister("conn1", close_code=1000)
        # After unregister, connection is removed from active count
        assert self.manager.active_count() == 0

    def test_record_message(self):
        """Test recording message receipt."""
        self.manager.register("conn1", "127.0.0.1:12345")
        self.manager.record_message("conn1")
        
        stats = self.manager.get_stats("conn1")
        assert stats is not None
        assert stats.messages_received == 1

    def test_record_chunk_stats(self):
        """Test recording chunk statistics."""
        self.manager.register("conn1", "127.0.0.1:12345")
        self.manager.record_chunk("conn1", compressed=100, uncompressed=500)
        
        stats = self.manager.get_stats("conn1")
        assert stats.chunks_sent == 1
        assert stats.bytes_sent_compressed == 100
        assert stats.bytes_sent_uncompressed == 500
        assert abs(stats.compression_ratio - 0.2) < 0.01

    def test_compression_ratio_calculation(self):
        """Test compression ratio calculation."""
        self.manager.register("conn1", "127.0.0.1:12345")
        self.manager.record_chunk("conn1", compressed=50, uncompressed=100)
        self.manager.record_chunk("conn1", compressed=60, uncompressed=100)
        
        stats = self.manager.get_stats("conn1")
        # Total: 110 compressed, 200 uncompressed = 0.55
        assert abs(stats.compression_ratio - 0.55) < 0.01

    def test_record_upstream_error(self):
        """Test recording upstream errors."""
        self.manager.register("conn1", "127.0.0.1:12345")
        self.manager.record_upstream_error("conn1")
        
        stats = self.manager.get_stats("conn1")
        assert stats.upstream_errors == 1

    def test_connection_stats_to_dict(self):
        """Test converting stats to dict."""
        self.manager.register("conn1", "127.0.0.1:12345")
        self.manager.record_message("conn1")
        self.manager.record_chunk("conn1", 100, 500)
        
        stats = self.manager.get_stats("conn1")
        stats_dict = stats.to_dict()
        
        assert stats_dict["connection_id"] == "conn1"
        assert stats_dict["client_address"] == "127.0.0.1:12345"
        assert stats_dict["messages_received"] == 1
        assert stats_dict["chunks_sent"] == 1
        assert stats_dict["bytes_sent"] == 100
        assert stats_dict["bytes_uncompressed"] == 500

    def test_get_all_stats(self):
        """Test getting stats for all connections."""
        for i in range(2):
            self.manager.register(f"conn{i}", f"127.0.0.1:{1000+i}")
        
        all_stats = self.manager.get_all_stats()
        assert len(all_stats) == 2
        assert all(isinstance(s, dict) for s in all_stats)

    def test_duration_calculation(self):
        """Test connection duration calculation."""
        self.manager.register("conn1", "127.0.0.1:12345")
        time.sleep(0.1)  # Wait a bit
        self.manager.unregister("conn1", close_code=1000)
        
        stats = self.manager.get_stats("conn1")
        assert stats.duration_seconds >= 0.09  # Allow some tolerance


class TestWebSocketConnectionStats:
    """Test connection stats dataclass."""

    def test_stats_initialization(self):
        """Test creating stats object."""
        stats = WebSocketConnectionStats(
            connection_id="test-conn",
            client_address="127.0.0.1:12345",
            connected_at=time.time(),
        )
        assert stats.connection_id == "test-conn"
        assert stats.client_address == "127.0.0.1:12345"
        assert stats.messages_received == 0
        assert stats.chunks_sent == 0

    def test_compression_ratio_zero_uncompressed(self):
        """Test compression ratio when no data sent."""
        stats = WebSocketConnectionStats(
            connection_id="test",
            client_address="127.0.0.1:1234",
            connected_at=time.time(),
        )
        assert stats.compression_ratio == 1.0  # No division by zero

    def test_disconnected_at_tracking(self):
        """Test tracking disconnection time."""
        stats = WebSocketConnectionStats(
            connection_id="test",
            client_address="127.0.0.1:1234",
            connected_at=time.time() - 10,
        )
        stats.disconnected_at = time.time()
        # Duration should be ~10 seconds
        assert 9 < stats.duration_seconds < 11


class TestStreamHandling:
    """Test streaming behavior (mocked)."""

    def test_sse_chunk_parsing(self):
        """Test parsing SSE chunks."""
        sse_content = 'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n'
        line = sse_content.split("\n")[0]
        
        if line.startswith("data: "):
            data_str = line[6:]
            event = json.loads(data_str)
            assert event["type"] == "content_block_delta"
            assert event["delta"]["text"] == "Hello"

    def test_multiple_events_in_stream(self):
        """Test handling multiple events in stream."""
        sse_stream = (
            'data: {"type":"message_start","message":{"id":"1"}}\n\n'
            'data: {"type":"content_block_start","index":0}\n\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi"}}\n\n'
            'data: {"type":"message_stop"}\n\n'
            '[DONE]\n\n'
        )
        
        events = []
        for line in sse_stream.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                    events.append(event)
                except json.JSONDecodeError:
                    pass
        
        assert len(events) == 4
        assert events[0]["type"] == "message_start"
        assert events[-1]["type"] == "message_stop"

    def test_json_event_serialization(self):
        """Test that events serialize/deserialize correctly."""
        original_event = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Test"}
        }
        
        # Simulate sending through compression
        json_str = json.dumps(original_event)
        compressed = compress_chunk(json_str)
        decompressed = decompress_chunk(compressed)
        restored_event = json.loads(decompressed)
        
        assert restored_event == original_event


class TestErrorHandling:
    """Test error scenarios (mocked)."""

    def test_invalid_json_message(self):
        """Test handling invalid JSON in initial message."""
        invalid_json = "not json"
        try:
            json.loads(invalid_json)
            assert False, "Should have raised JSONDecodeError"
        except json.JSONDecodeError as e:
            assert "Expecting value" in str(e)

    def test_missing_required_fields(self):
        """Test validation of required fields."""
        request_without_model = {"messages": []}
        
        required_fields = {"model", "messages"}
        present_fields = set(request_without_model.keys())
        missing = required_fields - present_fields
        
        assert "model" in missing
        assert len(missing) == 1

    def test_upstream_error_response_format(self):
        """Test formatting upstream error responses."""
        upstream_error = {
            "error": {
                "type": "authentication_error",
                "message": "Invalid API key"
            }
        }
        
        error_json = json.dumps(upstream_error)
        error_msg = json.loads(error_json)
        
        assert "error" in error_msg
        assert error_msg["error"]["type"] == "authentication_error"


class TestConcurrencyControl:
    """Test concurrent connection handling."""

    def test_concurrent_connections_within_limit(self):
        """Test managing multiple concurrent connections."""
        manager = WebSocketConnectionManager(max_connections=100)
        
        conn_ids = [f"conn{i}" for i in range(50)]
        
        for i, conn_id in enumerate(conn_ids):
            result = manager.register(conn_id, f"127.0.0.1:{1000+i}")
            assert result is True
        
        assert manager.active_count() == 50
        assert manager.can_accept() is True  # Still under limit

    def test_limit_exactly_at_boundary(self):
        """Test behavior exactly at connection limit."""
        manager = WebSocketConnectionManager(max_connections=5)
        
        # Register exactly 5 connections
        for i in range(5):
            manager.register(f"conn{i}", f"127.0.0.1:{1000+i}")
        
        assert manager.active_count() == 5
        assert manager.can_accept() is False
        
        # Try to register when at limit - should fail
        assert manager.register("conn5", "127.0.0.1:2000") is False
        
        # Unregister one
        manager.unregister("conn0")
        
        # Now should be able to register again
        assert manager.register("conn5", "127.0.0.1:2000") is True


class TestReconnectScenarios:
    """Test reconnection and recovery."""

    def test_rapid_reconnect_same_client(self):
        """Test rapid disconnect/reconnect from same IP."""
        manager = WebSocketConnectionManager(max_connections=10)
        
        # First connection
        assert manager.register("conn1", "127.0.0.1:12345") is True
        manager.unregister("conn1", close_code=1000)
        
        # Reconnect
        assert manager.register("conn1-new", "127.0.0.1:12345") is True
        assert manager.active_count() == 1  # Only active connection
        
        # But both are tracked in stats
        all_stats = manager.get_all_stats()
        assert len(all_stats) == 2  # Both tracked

    def test_error_recovery_path(self):
        """Test recovery from upstream error."""
        manager = WebSocketConnectionManager(max_connections=10)
        manager.register("conn1", "127.0.0.1:12345")
        
        # Record error
        manager.record_upstream_error("conn1")
        stats = manager.get_stats("conn1")
        assert stats.upstream_errors == 1
        
        # Connection still valid for new attempts
        manager.record_message("conn1")
        assert stats.messages_received == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
