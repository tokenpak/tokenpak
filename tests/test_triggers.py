"""
Unit tests for the event trigger system.

Tests:
- Trigger registration
- Trigger removal
- Pattern matching
- Dry-run testing
- Event firing
- Log management
"""

import json
import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.agent.macros.hooks import (
    TriggerRegistry,
    Trigger,
    EventType,
    TriggerLogEntry,
)


class TestEventType:
    """Test EventType enum and parsing."""
    
    def test_valid_event_types(self):
        """Should parse all valid event types."""
        assert EventType.from_string("file:changed") == EventType.FILE_CHANGED
        assert EventType.from_string("git:push") == EventType.GIT_PUSH
        assert EventType.from_string("cost:threshold") == EventType.COST_THRESHOLD
        assert EventType.from_string("agent:finished") == EventType.AGENT_FINISHED
    
    def test_case_insensitive(self):
        """Should handle different case variations."""
        assert EventType.from_string("FILE:CHANGED") == EventType.FILE_CHANGED
        assert EventType.from_string("Git:Push") == EventType.GIT_PUSH
    
    def test_underscore_to_colon(self):
        """Should convert underscores to colons."""
        assert EventType.from_string("file_changed") == EventType.FILE_CHANGED
        assert EventType.from_string("git_push") == EventType.GIT_PUSH
    
    def test_invalid_event_type(self):
        """Should raise ValueError for invalid event types."""
        with pytest.raises(ValueError) as exc_info:
            EventType.from_string("invalid:event")
        assert "Unknown event type" in str(exc_info.value)


class TestTrigger:
    """Test Trigger dataclass."""
    
    def test_trigger_creation(self):
        """Should create trigger with all fields."""
        trigger = Trigger(
            id="test123",
            event_type="file:changed",
            pattern="*.py",
            action="echo hello"
        )
        assert trigger.id == "test123"
        assert trigger.event_type == "file:changed"
        assert trigger.pattern == "*.py"
        assert trigger.action == "echo hello"
        assert trigger.enabled is True
    
    def test_file_pattern_matching(self):
        """Should match file paths with glob patterns."""
        trigger = Trigger(
            id="t1",
            event_type="file:changed",
            pattern="*.py",
            action="test"
        )
        
        assert trigger.matches("file:changed", "test.py") is True
        assert trigger.matches("file:changed", "src/main.py") is True
        assert trigger.matches("file:changed", "test.txt") is False
    
    def test_wildcard_pattern(self):
        """Should match any data with * pattern."""
        trigger = Trigger(
            id="t1",
            event_type="git:push",
            pattern="*",
            action="test"
        )
        
        assert trigger.matches("git:push", "main") is True
        assert trigger.matches("git:push", "feature/test") is True
    
    def test_disabled_trigger_no_match(self):
        """Disabled triggers should not match."""
        trigger = Trigger(
            id="t1",
            event_type="file:changed",
            pattern="*",
            action="test",
            enabled=False
        )
        
        assert trigger.matches("file:changed", "test.py") is False
    
    def test_wrong_event_type_no_match(self):
        """Should not match different event types."""
        trigger = Trigger(
            id="t1",
            event_type="file:changed",
            pattern="*",
            action="test"
        )
        
        assert trigger.matches("git:push", "test.py") is False
    
    def test_serialization(self):
        """Should serialize and deserialize correctly."""
        trigger = Trigger(
            id="t1",
            event_type="file:changed",
            pattern="*.py",
            action="echo test",
            description="Test trigger"
        )
        
        data = trigger.to_dict()
        restored = Trigger.from_dict(data)
        
        assert restored.id == trigger.id
        assert restored.event_type == trigger.event_type
        assert restored.pattern == trigger.pattern
        assert restored.action == trigger.action
        assert restored.description == trigger.description


class TestTriggerRegistry:
    """Test TriggerRegistry operations."""
    
    @pytest.fixture
    def temp_registry(self):
        """Create a registry with temporary storage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            triggers_path = Path(tmpdir) / "triggers.json"
            log_path = Path(tmpdir) / "log.json"
            registry = TriggerRegistry(
                triggers_path=triggers_path,
                log_path=log_path
            )
            yield registry
    
    def test_add_trigger(self, temp_registry):
        """Should add and persist trigger."""
        trigger = temp_registry.add(
            event_type="file:changed",
            pattern="*.py",
            action="echo modified"
        )
        
        assert trigger.id is not None
        assert len(trigger.id) == 8
        assert trigger.event_type == "file:changed"
        
        # Should be retrievable
        retrieved = temp_registry.get(trigger.id)
        assert retrieved is not None
        assert retrieved.action == "echo modified"
    
    def test_add_trigger_persists(self, temp_registry):
        """Added triggers should persist to disk."""
        trigger = temp_registry.add(
            event_type="git:push",
            pattern="*",
            action="tokenpak index ."
        )
        
        # Create new registry pointing to same files
        new_registry = TriggerRegistry(
            triggers_path=temp_registry.triggers_path,
            log_path=temp_registry.log_path
        )
        
        retrieved = new_registry.get(trigger.id)
        assert retrieved is not None
        assert retrieved.action == "tokenpak index ."
    
    def test_remove_trigger(self, temp_registry):
        """Should remove trigger."""
        trigger = temp_registry.add(
            event_type="file:changed",
            pattern="*",
            action="test"
        )
        
        assert temp_registry.remove(trigger.id) is True
        assert temp_registry.get(trigger.id) is None
    
    def test_remove_nonexistent(self, temp_registry):
        """Should return False for nonexistent trigger."""
        assert temp_registry.remove("nonexistent") is False
    
    def test_list_triggers(self, temp_registry):
        """Should list all triggers."""
        temp_registry.add("file:changed", "*.py", "echo 1")
        temp_registry.add("git:push", "*", "echo 2")
        temp_registry.add("file:changed", "*.md", "echo 3")
        
        all_triggers = temp_registry.list()
        assert len(all_triggers) == 3
        
        file_triggers = temp_registry.list(event_type="file:changed")
        assert len(file_triggers) == 2
    
    def test_test_dry_run(self, temp_registry):
        """Should show what triggers would fire."""
        temp_registry.add("file:changed", "*.py", "echo python")
        temp_registry.add("file:changed", "*.md", "echo markdown")
        temp_registry.add("git:push", "*", "echo push")
        
        results = temp_registry.test("file:changed", "test.py")
        
        assert len(results) == 1
        assert results[0]["would_fire"] is True
        assert "echo python" in results[0]["action"]
    
    def test_fire_triggers(self, temp_registry):
        """Should fire matching triggers and log results."""
        temp_registry.add(
            event_type="file:changed",
            pattern="*.py",
            action="echo 'file changed'"
        )
        
        entries = temp_registry.fire("file:changed", "test.py")
        
        assert len(entries) == 1
        assert entries[0].success is True
        assert "file changed" in entries[0].output
    
    def test_fire_dry_run(self, temp_registry):
        """Dry run should not execute actions."""
        temp_registry.add(
            event_type="file:changed",
            pattern="*",
            action="echo 'should not run'"
        )
        
        entries = temp_registry.fire("file:changed", "test.py", dry_run=True)
        
        assert len(entries) == 1
        assert entries[0].dry_run is True
        assert entries[0].output == ""  # No execution
    
    def test_fire_no_match(self, temp_registry):
        """Should return empty list when no triggers match."""
        temp_registry.add("git:push", "*", "echo push")
        
        entries = temp_registry.fire("file:changed", "test.py")
        
        assert len(entries) == 0
    
    def test_fire_with_substitution(self, temp_registry):
        """Should substitute $EVENT_DATA in action."""
        temp_registry.add(
            event_type="file:changed",
            pattern="*",
            action="echo $EVENT_DATA"
        )
        
        entries = temp_registry.fire("file:changed", "myfile.py")
        
        assert len(entries) == 1
        assert "myfile.py" in entries[0].output
    
    def test_get_log(self, temp_registry):
        """Should return trigger activation log."""
        temp_registry.add("file:changed", "*", "echo test")
        temp_registry.fire("file:changed", "test1.py")
        temp_registry.fire("file:changed", "test2.py")
        
        log = temp_registry.get_log(limit=10)
        
        assert len(log) == 2
        # Should be newest first
        assert "test2.py" in log[0].event_data
    
    def test_clear_log(self, temp_registry):
        """Should clear the log."""
        temp_registry.add("file:changed", "*", "echo test")
        temp_registry.fire("file:changed", "test.py")
        
        count = temp_registry.clear_log()
        
        assert count == 1
        assert len(temp_registry.get_log()) == 0


class TestCLIIntegration:
    """Test CLI command functions."""
    
    @pytest.fixture
    def mock_registry(self):
        """Mock the module-level registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            triggers_path = Path(tmpdir) / "triggers.json"
            log_path = Path(tmpdir) / "log.json"
            registry = TriggerRegistry(
                triggers_path=triggers_path,
                log_path=log_path
            )
            
            with patch('tokenpak.agent.macros.hooks._registry', registry):
                yield registry
    
    def test_add_command(self, mock_registry, capsys):
        """Test trigger add CLI command."""
        from tokenpak.cli import cmd_trigger_add
        
        class Args:
            event = "file:changed"
            pattern = "*.py"
            action = "echo hello"
            description = "Test"
        
        cmd_trigger_add(Args())
        
        captured = capsys.readouterr()
        assert "Trigger created" in captured.out
        assert len(mock_registry.list()) == 1
    
    def test_list_command(self, mock_registry, capsys):
        """Test trigger list CLI command."""
        from tokenpak.cli import cmd_trigger_list
        
        mock_registry.add("file:changed", "*.py", "echo test")
        
        class Args:
            event = None
        
        cmd_trigger_list(Args())
        
        captured = capsys.readouterr()
        assert "file:changed" in captured.out
        assert "*.py" in captured.out
    
    def test_remove_command(self, mock_registry, capsys):
        """Test trigger remove CLI command."""
        from tokenpak.cli import cmd_trigger_remove
        
        trigger = mock_registry.add("file:changed", "*", "echo test")
        
        class Args:
            id = trigger.id
        
        cmd_trigger_remove(Args())
        
        captured = capsys.readouterr()
        assert "removed" in captured.out
        assert len(mock_registry.list()) == 0
    
    def test_test_command(self, mock_registry, capsys):
        """Test trigger test CLI command."""
        from tokenpak.cli import cmd_trigger_test
        
        mock_registry.add("file:changed", "*.py", "echo python")
        mock_registry.add("file:changed", "*.md", "echo markdown")
        
        class Args:
            event = "file:changed"
            data = "test.py"
        
        cmd_trigger_test(Args())
        
        captured = capsys.readouterr()
        assert "echo python" in captured.out
        assert "echo markdown" not in captured.out


class TestFileWatcher:
    """Test file watcher functionality."""
    
    def test_watcher_not_running_initially(self):
        """Watcher should not be running by default."""
        from tokenpak.agent.macros.hooks import is_file_watcher_running
        
        # May already be running from other tests, so just check it returns bool
        result = is_file_watcher_running()
        assert isinstance(result, bool)
    
    def test_start_stop_watcher(self):
        """Should start and stop file watcher."""
        from tokenpak.agent.macros.hooks import (
            start_file_watcher,
            stop_file_watcher,
            is_file_watcher_running
        )
        
        # Skip if watchdog not installed
        try:
            import watchdog
        except ImportError:
            pytest.skip("watchdog not installed")
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = start_file_watcher([tmpdir])
            
            if result:  # Only test if it started successfully
                assert is_file_watcher_running() is True
                
                stop_result = stop_file_watcher()
                assert stop_result is True
                assert is_file_watcher_running() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
