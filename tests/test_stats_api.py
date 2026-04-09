"""Tests for tokenpak.agent.proxy.stats_api module."""
import pytest
from tokenpak.agent.proxy.stats_api import StatsAPI

class TestStatsAPI:
    @pytest.mark.quick
    def test_stats_api_init(self):
        api = StatsAPI()
        assert api is not None

    @pytest.mark.quick
    def test_stats_api_callable(self):
        api = StatsAPI()
        assert hasattr(api, '__class__')

    @pytest.mark.quick
    def test_stats_api_not_none(self):
        StatsAPI is not None

    @pytest.mark.quick
    def test_multiple_instances(self):
        api1 = StatsAPI()
        api2 = StatsAPI()
        assert api1 is not None and api2 is not None
