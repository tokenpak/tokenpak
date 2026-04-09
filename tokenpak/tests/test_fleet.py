"""Unit tests for fleet.py — fleet configuration and query operations."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import yaml

from tokenpak.fleet import (
    FleetMachine,
    FleetStats,
    FleetAgentRow,
    _get_fleet_config_path,
    load_fleet_config,
    save_fleet_config,
    _query_machine_aggregate,
    query_fleet_agent_rows,
    _query_machine,
    query_fleet,
    _fmt_cost,
    _fmt_tokens,
    _calc_savings,
    render_fleet_table,
    render_fleet_agent_table,
    render_fleet_json,
    interactive_add_machine,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_machine():
    """Sample FleetMachine for testing."""
    return FleetMachine(name="test-machine", host="localhost", port=8766)


@pytest.fixture
def sample_machines():
    """Sample list of FleetMachines."""
    return [
        FleetMachine(name="sue", host="192.168.1.100", port=8766),
        FleetMachine(name="trix", host="192.168.1.101", port=8766),
    ]


@pytest.fixture
def sample_stats():
    """Sample FleetStats for testing."""
    return FleetStats(
        name="test-machine",
        requests=100,
        saved=5000,
        cache_pct=25.5,
        compression=15.2,
        health="✅",
        cost=12.50,
        cost_saved=3.20,
        cache_read_tokens=10000,
    )


@pytest.fixture
def sample_fleet_agent_rows():
    """Sample FleetAgentRow list."""
    return [
        FleetAgentRow(
            machine="sue",
            agent="claude",
            model="sonnet",
            requests=50,
            tokens=5000,
            cost=2.50,
            saved=0.50,
        ),
        FleetAgentRow(
            machine="trix",
            agent="cali",
            model="haiku",
            requests=25,
            tokens=1000,
            cost=0.30,
            saved=0.10,
        ),
    ]


# ── FleetMachine Tests ────────────────────────────────────────────────────────


class TestFleetMachine:
    """Test FleetMachine dataclass."""

    def test_create_fleet_machine(self):
        """Test creating a FleetMachine."""
        machine = FleetMachine(name="test", host="localhost", port=8766)
        assert machine.name == "test"
        assert machine.host == "localhost"
        assert machine.port == 8766

    def test_fleet_machine_to_dict(self):
        """Test FleetMachine.to_dict()."""
        machine = FleetMachine(name="sue", host="192.168.1.100", port=9000)
        d = machine.to_dict()
        assert d == {"name": "sue", "host": "192.168.1.100", "port": 9000}

    def test_fleet_machine_multiple_ports(self):
        """Test FleetMachine with different ports."""
        machine1 = FleetMachine(name="worker-1", host="host-1", port=8766)
        machine2 = FleetMachine(name="worker-2", host="host-2", port=9000)
        assert machine1.port == 8766
        assert machine2.port == 9000


# ── FleetStats Tests ──────────────────────────────────────────────────────────


class TestFleetStats:
    """Test FleetStats dataclass."""

    def test_create_fleet_stats(self, sample_stats):
        """Test creating FleetStats."""
        assert sample_stats.name == "test-machine"
        assert sample_stats.requests == 100
        assert sample_stats.health == "✅"
        assert sample_stats.error is None

    def test_fleet_stats_with_error(self):
        """Test FleetStats with error."""
        stats = FleetStats(
            name="broken",
            health="❌",
            error="Connection refused",
        )
        assert stats.health == "❌"
        assert stats.error == "Connection refused"

    def test_fleet_stats_empty_defaults(self):
        """Test FleetStats with only name (rest should default)."""
        stats = FleetStats(name="empty")
        assert stats.requests == 0
        assert stats.saved == 0
        assert stats.cache_pct == 0.0
        assert stats.compression == 0.0
        assert stats.health == "❌"


# ── Config I/O Tests ──────────────────────────────────────────────────────────


class TestFleetConfig:
    """Test fleet configuration loading/saving."""

    def test_load_fleet_config_empty(self):
        """Test loading when no config file exists."""
        with patch("tokenpak.fleet._get_fleet_config_path") as mock_path:
            mock_path.return_value = Path("/nonexistent/fleet.yaml")
            machines = load_fleet_config()
            assert machines == []

    def test_load_fleet_config_from_file(self):
        """Test loading fleet.yaml with valid config."""
        config_data = {
            "fleet": [
                {"name": "sue", "host": "192.168.1.100", "port": 8766},
                {"name": "trix", "host": "192.168.1.101", "port": 8767},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "fleet.yaml"
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

            with patch("tokenpak.fleet._get_fleet_config_path") as mock_path:
                mock_path.return_value = config_path
                machines = load_fleet_config()

        assert len(machines) == 2
        assert machines[0].name == "sue"
        assert machines[0].host == "192.168.1.100"
        assert machines[1].name == "trix"
        assert machines[1].port == 8767

    def test_save_fleet_config(self, sample_machines):
        """Test saving machines to fleet.yaml."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "fleet.yaml"

            with patch("tokenpak.fleet._get_fleet_config_path") as mock_path:
                mock_path.return_value = config_path
                save_fleet_config(sample_machines)

            # Read back and verify
            with open(config_path, "r") as f:
                data = yaml.safe_load(f)

            assert "fleet" in data
            assert len(data["fleet"]) == 2
            assert data["fleet"][0]["name"] == "sue"
            assert data["fleet"][1]["port"] == 8766

    def test_load_fleet_config_legacy_agents_key(self):
        """Test loading with old 'agents' key."""
        config_data = {
            "agents": [
                {"name": "legacy", "host": "oldhost", "port": 9000},
            ]
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "fleet.yaml"
            with open(config_path, "w") as f:
                yaml.dump(config_data, f)

            with patch("tokenpak.fleet._get_fleet_config_path") as mock_path:
                mock_path.return_value = config_path
                machines = load_fleet_config()

        assert len(machines) == 1
        assert machines[0].name == "legacy"
        assert machines[0].port == 9000


# ── Health & Stats Query Tests ────────────────────────────────────────────────


class TestQueryMachine:
    """Test single machine query operations."""

    def test_query_machine_healthy(self, sample_machine):
        """Test querying a healthy machine."""
        health_response = {
            "status": "ok",
            "timestamp": 1234567890,
        }
        stats_response = {
            "session": {
                "requests": 42,
                "saved_tokens": 1000,
                "input_tokens": 10000,
                "sent_input_tokens": 8000,
                "output_tokens": 5000,
                "sent_output_tokens": 4000,
                "cost": 5.25,
                "cost_saved": 1.10,
                "cache_read_tokens": 2000,
            }
        }

        def mock_urlopen(url, timeout=None):
            response = MagicMock()
            if "/health" in url:
                response.read.return_value = json.dumps(health_response).encode()
            elif "/stats" in url:
                response.read.return_value = json.dumps(stats_response).encode()
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=None)
            return response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            stats = _query_machine(sample_machine)

        assert stats.name == "test-machine"
        assert stats.health == "✅"
        assert stats.requests == 42
        assert stats.saved == 1000
        assert stats.cost == 5.25
        assert stats.cost_saved == 1.10
        assert stats.cache_read_tokens == 2000
        assert stats.cache_pct == 20.0  # (10000-8000)/10000 * 100
        assert stats.compression == 20.0  # (5000-4000)/5000 * 100

    def test_query_machine_degraded(self, sample_machine):
        """Test querying a degraded machine."""
        health_response = {"status": "degraded"}
        stats_response = {"session": {}}

        def mock_urlopen(url, timeout=None):
            response = MagicMock()
            response.read.return_value = (
                json.dumps(health_response).encode()
                if "/health" in url
                else json.dumps(stats_response).encode()
            )
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=None)
            return response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            stats = _query_machine(sample_machine)

        assert stats.health == "⚠️"

    def test_query_machine_offline(self, sample_machine):
        """Test querying an offline machine."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection refused")
            stats = _query_machine(sample_machine)

        assert stats.health == "❌"
        assert stats.error is not None

    def test_query_machine_zero_tokens(self, sample_machine):
        """Test cache_pct with zero input tokens."""
        health_response = {"status": "ok"}
        stats_response = {
            "session": {
                "input_tokens": 0,
                "sent_input_tokens": 0,
                "output_tokens": 0,
                "sent_output_tokens": 0,
            }
        }

        def mock_urlopen(url, timeout=None):
            response = MagicMock()
            response.read.return_value = (
                json.dumps(health_response).encode()
                if "/health" in url
                else json.dumps(stats_response).encode()
            )
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=None)
            return response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            stats = _query_machine(sample_machine)

        assert stats.cache_pct == 0.0  # no division by zero
        assert stats.compression == 0.0


class TestQueryFleet:
    """Test fleet-wide query operations."""

    def test_query_fleet(self, sample_machines):
        """Test querying multiple machines."""
        responses = [
            {"status": "ok"},  # for /health
            {"session": {"requests": 50}},  # for /stats
            {"status": "ok"},
            {"session": {"requests": 30}},
        ]
        response_iter = iter(responses)

        def mock_urlopen(url, timeout=None):
            response = MagicMock()
            response.read.return_value = json.dumps(next(response_iter)).encode()
            response.__enter__ = MagicMock(return_value=response)
            response.__exit__ = MagicMock(return_value=None)
            return response

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            results = query_fleet(sample_machines)

        assert len(results) == 2
        assert results[0].name == "sue"
        assert results[1].name == "trix"


class TestQueryMachineAggregate:
    """Test per-agent query operations."""

    def test_query_machine_aggregate_success(self, sample_machine):
        """Test successful aggregate query."""
        response = {
            "rows": [
                {
                    "machine": "test-machine",
                    "agent": "sue",
                    "model": "sonnet",
                    "requests": 100,
                    "tokens": 10000,
                    "cost": 5.0,
                    "saved": 1.0,
                }
            ]
        }

        def mock_urlopen(url, timeout=None):
            resp = MagicMock()
            resp.read.return_value = json.dumps(response).encode()
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=None)
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            rows, err = _query_machine_aggregate(sample_machine)

        assert len(rows) == 1
        assert rows[0]["agent"] == "sue"
        assert err is None

    def test_query_machine_aggregate_error(self, sample_machine):
        """Test aggregate query with error."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Timeout")
            rows, err = _query_machine_aggregate(sample_machine)

        assert rows == []
        assert err is not None

    def test_query_fleet_agent_rows(self, sample_machines):
        """Test querying agent rows across fleet."""
        responses = [
            {
                "rows": [
                    {
                        "machine": "sue",
                        "agent": "a1",
                        "model": "sonnet",
                        "requests": 50,
                        "tokens": 5000,
                        "cost": 2.5,
                        "saved": 0.5,
                    }
                ]
            },
            {
                "rows": [
                    {
                        "machine": "trix",
                        "agent": "a2",
                        "model": "haiku",
                        "requests": 30,
                        "tokens": 1000,
                        "cost": 0.3,
                        "saved": 0.1,
                    }
                ]
            },
        ]
        response_iter = iter(responses)

        def mock_urlopen(url, timeout=None):
            resp = MagicMock()
            resp.read.return_value = json.dumps(next(response_iter)).encode()
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=None)
            return resp

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            rows, errors = query_fleet_agent_rows(sample_machines)

        assert len(rows) == 2
        assert rows[0].agent == "a1"
        assert rows[1].agent == "a2"
        assert errors == []


# ── Formatting Tests ──────────────────────────────────────────────────────────


class TestFormatting:
    """Test output formatting functions."""

    def test_fmt_cost_dollars(self):
        """Test formatting costs >= $1."""
        assert _fmt_cost(5.25) == "$5.25"
        assert _fmt_cost(0.50) == "$0.50"
        assert _fmt_cost(0.0001) == "$0.0001"

    def test_fmt_cost_cents(self):
        """Test formatting costs < $0.01."""
        assert _fmt_cost(0.001) == "$0.0010"

    def test_fmt_tokens_millions(self):
        """Test formatting token counts in millions."""
        assert _fmt_tokens(1_500_000) == "1.5M"
        assert _fmt_tokens(10_000_000) == "10.0M"

    def test_fmt_tokens_thousands(self):
        """Test formatting token counts in thousands."""
        assert _fmt_tokens(5_000) == "5K"
        assert _fmt_tokens(100_000) == "100K"

    def test_fmt_tokens_small(self):
        """Test formatting small token counts."""
        assert _fmt_tokens(42) == "42"
        assert _fmt_tokens(0) == "0"

    def test_calc_savings_no_compression(self):
        """Test savings calculation with no compression."""
        stats = FleetStats(name="test", saved=0, cache_read_tokens=0)
        comp, cache, total = _calc_savings(stats)
        assert comp == 0.0
        assert cache == 0.0
        assert total == 0.0

    def test_calc_savings_with_compression(self):
        """Test savings calculation with compression."""
        stats = FleetStats(
            name="test",
            saved=1_000_000,  # 1M tokens saved
            cache_read_tokens=1_000_000,  # 1M tokens from cache
        )
        comp, cache, total = _calc_savings(stats)
        # Compression: (1M / 1M) * $3 = $3
        assert comp == pytest.approx(3.0, rel=0.01)
        # Cache: (1M / 1M) * $3 * 0.9 = $2.7
        assert cache == pytest.approx(2.7, rel=0.01)
        assert total == pytest.approx(5.7, rel=0.01)

    def test_calc_savings_custom_rate(self):
        """Test savings calculation with custom input rate."""
        stats = FleetStats(
            name="test",
            saved=1_000_000,
            cache_read_tokens=0,
        )
        with patch.dict("os.environ", {"TOKENPAK_INPUT_RATE": "10.0"}):
            comp, cache, total = _calc_savings(stats)
            assert comp == pytest.approx(10.0, rel=0.01)


class TestRenderFleetTable:
    """Test fleet table rendering."""

    def test_render_fleet_table_empty(self):
        """Test rendering with no machines."""
        output = render_fleet_table([])
        assert "No machines configured" in output

    def test_render_fleet_table_single(self, sample_stats):
        """Test rendering a single machine's stats."""
        output = render_fleet_table([sample_stats])
        assert "test-machine" in output
        assert "100 reqs" in output
        assert "✅" in output

    def test_render_fleet_table_multiple(self):
        """Test rendering multiple machines."""
        stats1 = FleetStats(
            name="sue",
            requests=100,
            health="✅",
            cost=10.0,
            saved=2.0,
            cache_read_tokens=1000000,
        )
        stats2 = FleetStats(
            name="trix",
            requests=50,
            health="✅",
            cost=5.0,
            saved=1.0,
            cache_read_tokens=500000,
        )
        output = render_fleet_table([stats1, stats2])
        assert "sue" in output
        assert "trix" in output
        assert "Fleet:" in output

    def test_render_fleet_agent_table_empty(self):
        """Test rendering with no agent rows."""
        output = render_fleet_agent_table([])
        assert "No fleet agent data" in output

    def test_render_fleet_agent_table(self, sample_fleet_agent_rows):
        """Test rendering agent rows."""
        output = render_fleet_agent_table(sample_fleet_agent_rows)
        assert "sue" in output
        assert "trix" in output
        assert "TOTAL" in output
        assert "claude" in output
        assert "cali" in output

    def test_render_fleet_json(self, sample_stats):
        """Test JSON rendering."""
        output = render_fleet_json([sample_stats])
        data = json.loads(output)
        assert "machines" in data
        assert "timestamp" in data
        assert "totals" in data
        assert len(data["machines"]) == 1
        assert data["machines"][0]["name"] == "test-machine"


# ── Interactive Setup Tests ───────────────────────────────────────────────────


class TestInteractiveAddMachine:
    """Test interactive machine addition."""

    def test_interactive_add_machine_success(self):
        """Test successfully adding a machine."""
        health_response = {"status": "ok"}
        stats_response = {"session": {}}

        def mock_urlopen(url, timeout=None):
            resp = MagicMock()
            resp.read.return_value = (
                json.dumps(health_response).encode()
                if "/health" in url
                else json.dumps(stats_response).encode()
            )
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=None)
            return resp

        with patch("builtins.input") as mock_input:
            mock_input.side_effect = ["newmachine", "192.168.1.50", "8766"]
            with patch("urllib.request.urlopen", side_effect=mock_urlopen):
                machine = interactive_add_machine([])

        assert machine is not None
        assert machine.name == "newmachine"
        assert machine.host == "192.168.1.50"
        assert machine.port == 8766

    def test_interactive_add_machine_duplicate(self):
        """Test adding a duplicate machine."""
        existing = [FleetMachine(name="sue", host="localhost", port=8766)]
        with patch("builtins.input") as mock_input:
            mock_input.return_value = "sue"
            machine = interactive_add_machine(existing)

        assert machine is None

    def test_interactive_add_machine_no_name(self):
        """Test cancelling due to no name."""
        with patch("builtins.input") as mock_input:
            mock_input.return_value = ""
            machine = interactive_add_machine([])

        assert machine is None

    def test_interactive_add_machine_invalid_port(self):
        """Test invalid port input."""
        with patch("builtins.input") as mock_input:
            mock_input.side_effect = ["machine", "localhost", "invalid"]
            machine = interactive_add_machine([])

        assert machine is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
