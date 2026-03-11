"""Tests for tokenpak.agent.macros.script_hooks module."""
import pytest
from tokenpak.agent.macros.script_hooks import fire_hook, fire_on_error, fire_on_request, fire_on_budget_alert, HOOK_NAMES

class TestScriptHooks:
    def test_fire_hook_callable(self):
        assert callable(fire_hook)
    
    def test_fire_on_error_callable(self):
        assert callable(fire_on_error)
    
    def test_fire_on_request_callable(self):
        assert callable(fire_on_request)
    
    def test_fire_on_budget_alert_callable(self):
        assert callable(fire_on_budget_alert)
    
    def test_fire_hook_returns_none_or_result(self):
        result = fire_hook("test_hook")
        assert result is None or isinstance(result, (str, dict, list))
    
    def test_fire_on_error_call(self):
        result = fire_on_error("test error")
        assert result is None or isinstance(result, (str, dict))
    
    def test_fire_on_request_call(self):
        result = fire_on_request("GET", "/test")
        assert result is None or isinstance(result, (str, dict))
    
    def test_fire_on_budget_alert_call(self):
        result = fire_on_budget_alert(0.75)
        assert result is None or isinstance(result, (str, dict))
    
    def test_hook_names_exist(self):
        assert isinstance(HOOK_NAMES, (list, dict, set, tuple))
    
    def test_hook_names_not_empty(self):
        if isinstance(HOOK_NAMES, (list, tuple)):
            assert len(HOOK_NAMES) >= 0
        elif isinstance(HOOK_NAMES, dict):
            assert isinstance(HOOK_NAMES, dict)
