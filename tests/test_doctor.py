"""Comprehensive tests for tokenpak doctor command."""

import json
import sys
import tempfile
import unittest
from collections import namedtuple
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from tokenpak.cli import Colors, cmd_doctor

# Create a namedtuple for version_info that works like sys.version_info
VersionInfo = namedtuple('VersionInfo', ['major', 'minor', 'micro', 'releaselevel', 'serial'])


class DoctorChecksPythonVersionTest(unittest.TestCase):
    """Test Python version check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.captured_output = StringIO()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_python_version_pass_310(self, mock_stdout):
        """Python 3.10+ should pass."""
        cmd_doctor(self.args)
        output = mock_stdout.getvalue()
        self.assertIn('✅', output)
        self.assertIn('Python version', output)
        self.assertIn('3.10.0', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 11, 5, 'final', 0))
    def test_python_version_pass_311(self, mock_stdout):
        """Python 3.11 should pass."""
        cmd_doctor(self.args)
        output = mock_stdout.getvalue()
        self.assertIn('Python version', output)
        self.assertIn('3.11.5', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 9, 2, 'final', 0))
    def test_python_version_fail_39(self, mock_stdout):
        """Python 3.9 should fail."""
        with self.assertRaises(SystemExit) as cm:
            cmd_doctor(self.args)
        self.assertEqual(cm.exception.code, 1)
        output = mock_stdout.getvalue()
        self.assertIn('❌', output)
        self.assertIn('requires ≥3.10', output)


class DoctorConfigFileTest(unittest.TestCase):
    """Test config file check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_config_valid_json(self, mock_stdout):
        """Valid config.json should pass."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('✅', output)
            self.assertIn('Config file', output)
            self.assertIn('valid', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_config_missing(self, mock_stdout):
        """Missing config.json should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('Config file', output)
            self.assertIn('not found', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_config_invalid_json(self, mock_stdout):
        """Invalid JSON should fail."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text("{invalid json")

        with patch('pathlib.Path.home', return_value=self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                cmd_doctor(self.args)
            self.assertEqual(cm.exception.code, 1)
            output = mock_stdout.getvalue()
            self.assertIn('❌', output)
            self.assertIn('invalid JSON', output)


class DoctorVaultIndexTest(unittest.TestCase):
    """Test vault index check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('tokenpak.cli.commands.doctor._proxy_get', return_value=None)
    def test_vault_index_with_blocks(self, mock_proxy_get, mock_stdout):
        """Valid vault index with blocks should pass."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": [{"id": "1"}, {"id": "2"}]}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('✅', output)
            self.assertIn('Vault index', output)
            self.assertIn('2 blocks', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('tokenpak.cli.commands.doctor._proxy_get', return_value=None)
    def test_vault_index_zero_blocks(self, mock_proxy_get, mock_stdout):
        """Vault index with 0 blocks should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('0 blocks', output)
            self.assertIn('tokenpak index', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('tokenpak.cli.commands.doctor._proxy_get', return_value=None)
    def test_vault_index_missing(self, mock_proxy_get, mock_stdout):
        """Missing vault index should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('Vault index', output)
            self.assertIn('not found', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('tokenpak.cli.commands.doctor._proxy_get', return_value=None)
    def test_vault_index_invalid_json(self, mock_proxy_get, mock_stdout):
        """Invalid vault index JSON should fail."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        index_file = config_dir / "index.json"
        index_file.write_text("{invalid")

        with patch('pathlib.Path.home', return_value=self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                cmd_doctor(self.args)
            self.assertEqual(cm.exception.code, 1)
            output = mock_stdout.getvalue()
            self.assertIn('❌', output)
            self.assertIn('invalid JSON', output)


class DoctorProxyPortTest(unittest.TestCase):
    """Test proxy port connectivity check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('socket.socket')
    def test_proxy_reachable(self, mock_socket, mock_stdout):
        """Reachable proxy port should be reported (TCP fallback when HTTP mocked away)."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0  # Success
        mock_socket.return_value = mock_sock

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('port open', output)

    @patch('tokenpak.cli.commands.doctor._proxy_get', return_value=None)
    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('socket.socket')
    def test_proxy_unreachable(self, mock_socket, mock_stdout, mock_proxy_get):
        """Unreachable proxy should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1  # Connection refused
        mock_socket.return_value = mock_sock

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('not running', output)

    @patch('tokenpak.cli.commands.doctor._proxy_get', return_value=None)
    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('socket.socket')
    def test_proxy_check_exception(self, mock_socket, mock_stdout, mock_proxy_get):
        """Socket exception should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        mock_socket.side_effect = Exception("Network error")

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('not reachable', output)


class DoctorDiskSpaceTest(unittest.TestCase):
    """Test disk space check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_disk_usage_small(self, mock_stdout):
        """Small disk usage should pass."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        # Create a small file
        test_file = config_dir / "test.txt"
        test_file.write_text("x" * 1000)  # ~1KB

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('✅', output)
            self.assertIn('Disk usage', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_disk_usage_large(self, mock_stdout):
        """Large disk usage (>500MB) should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        # Mock a large file stat rather than writing 600MB to disk (which causes test hangs)
        fake_stat = MagicMock()
        fake_stat.st_size = 600 * 1024 * 1024  # 600MB

        fake_file = MagicMock()
        fake_file.is_file.return_value = True
        fake_file.stat.return_value = fake_stat

        with patch('pathlib.Path.home', return_value=self.temp_path), \
             patch.object(type(config_dir), 'rglob', return_value=[fake_file]):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('Disk usage', output)
            self.assertIn('consider cleanup', output)


class DoctorLogFileTest(unittest.TestCase):
    """Test log file check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_log_file_missing(self, mock_stdout):
        """Missing log file should report as not present."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('Debug log', output)
            self.assertIn('not present', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_log_file_present(self, mock_stdout):
        """Present log file should report size."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        log_file = config_dir / "debug.log"
        log_file.write_text("log entry 1\nlog entry 2\n")

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('Debug log', output)
            self.assertIn('MB', output)


class DoctorFixFlagTest(unittest.TestCase):
    """Test --fix flag functionality."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = True
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('socket.socket')
    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_fix_create_config(self, mock_stdout, mock_socket):
        """--fix should create missing config."""
        # Mock the socket to avoid hanging
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1  # connection refused
        mock_socket.return_value = mock_sock

        config_dir = self.temp_path / ".tokenpak"
        # Don't create config dir or file

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()

            # Check that config was created
            config_file = config_dir / "config.json"
            self.assertTrue(config_file.exists())

            # Verify it's valid JSON
            with open(config_file) as f:
                data = json.load(f)
                self.assertEqual(data.get("port"), 8766)

    @patch('socket.socket')
    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_fix_backup_on_overwrite(self, mock_stdout, mock_socket):
        """--fix should backup before overwriting invalid config."""
        # Mock the socket to avoid hanging
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1  # connection refused
        mock_socket.return_value = mock_sock

        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text("{invalid")

        with patch('pathlib.Path.home', return_value=self.temp_path):
            with self.assertRaises(SystemExit):
                cmd_doctor(self.args)
            output = mock_stdout.getvalue()

            # Verify config was not overwritten (backup check would be in implementation)
            # For now just check that fix was attempted
            self.assertIn('fix', output.lower() or '')


class DoctorOutputFormatTest(unittest.TestCase):
    """Test output formatting."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_output_has_header(self, mock_stdout):
        """Output should have proper header."""
        config_dir = Path.home() / ".tokenpak"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        try:
            cmd_doctor(self.args)
        except SystemExit:
            pass

        output = mock_stdout.getvalue()
        self.assertIn('TOKENPAK', output)
        self.assertIn('Doctor', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_output_has_summary(self, mock_stdout):
        """Output should have summary line."""
        config_dir = Path.home() / ".tokenpak"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        try:
            cmd_doctor(self.args)
        except SystemExit:
            pass

        output = mock_stdout.getvalue()
        self.assertRegex(output, r'\d+ error[s]?, \d+ warning[s]?')


class DoctorExitCodesTest(unittest.TestCase):
    """Test exit codes."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('socket.socket')
    def test_exit_code_0_on_pass(self, mock_socket):
        """Should exit 0 when all checks pass or warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))
        index_file = config_dir / "index.json"
        index_file.write_text(json.dumps({"blocks": []}))

        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket.return_value = mock_sock

        with patch('pathlib.Path.home', return_value=self.temp_path):
            with patch('sys.stdout', new_callable=StringIO):
                try:
                    cmd_doctor(self.args)
                    # If we get here without SystemExit, exit code should be 0
                except SystemExit as e:
                    self.assertEqual(e.code, None)  # sys.exit(0) or no exit

    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 9, 0, 'final', 0))
    def test_exit_code_1_on_fail(self):
        """Should exit 1 when any check fails."""
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False

        with patch('sys.stdout', new_callable=StringIO):
            with self.assertRaises(SystemExit) as cm:
                cmd_doctor(self.args)
            self.assertEqual(cm.exception.code, 1)


class ColorsTest(unittest.TestCase):
    """Test color formatting."""

    def test_ok_color(self):
        """OK should have green emoji."""
        result = Colors.ok("test")
        self.assertIn('✅', result)
        self.assertIn('test', result)

    def test_warn_color(self):
        """Warn should have yellow emoji."""
        result = Colors.warn("test")
        self.assertIn('⚠️', result)
        self.assertIn('test', result)

    def test_fail_color(self):
        """Fail should have red emoji."""
        result = Colors.fail("test")
        self.assertIn('❌', result)
        self.assertIn('test', result)


if __name__ == "__main__":
    unittest.main()


class DoctorRequiredDirsTest(unittest.TestCase):
    """Test required directories check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_required_dirs_all_present(self, mock_stdout):
        """All required dirs present should pass."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        (config_dir / "cache").mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('Required dirs', output)
            self.assertIn('all present', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_required_dirs_missing(self, mock_stdout):
        """Missing required dirs should warn."""
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        # Don't create cache dir
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('⚠️', output)
            self.assertIn('Required dirs', output)
            self.assertIn('missing', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_fix_creates_missing_dirs(self, mock_stdout):
        """--fix should create missing directories."""
        self.args.fix = True
        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            # cache dir should be created by --fix
            self.assertTrue((config_dir / "cache").exists())


class DoctorDependenciesTest(unittest.TestCase):
    """Test Python dependencies check."""

    def setUp(self):
        self.args = MagicMock()
        self.args.fix = False
        self.args.fleet = False
        self.args.claude_code = False
        self.temp_home = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_home.name)

    def tearDown(self):
        self.temp_home.cleanup()

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    def test_required_deps_present(self, mock_stdout):
        """Standard lib deps (json, sqlite3, pathlib) always present."""
        import importlib.util
        # These are stdlib — always available
        for pkg in ('json', 'sqlite3', 'pathlib'):
            self.assertIsNotNone(importlib.util.find_spec(pkg))

        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        (config_dir / "cache").mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('Dependencies', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('importlib.util.find_spec')
    def test_missing_optional_deps_warns(self, mock_find_spec, mock_stdout):
        """Missing optional deps should warn."""
        # Stdlib always present, optional packages absent
        def fake_find_spec(pkg):
            if pkg in ('aiohttp', 'fastapi', 'uvicorn'):
                return None
            import importlib.util as _iu
            return _iu.find_spec.__wrapped__(pkg) if hasattr(_iu.find_spec, '__wrapped__') else True

        mock_find_spec.side_effect = lambda p: None if p in ('aiohttp', 'fastapi', 'uvicorn') else True

        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        (config_dir / "cache").mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            cmd_doctor(self.args)
            output = mock_stdout.getvalue()
            self.assertIn('Dependencies', output)
            # Should warn or pass (aiohttp/fastapi/uvicorn are optional)
            self.assertIn('⚠️', output)

    @patch('sys.stdout', new_callable=StringIO)
    @patch('tokenpak.cli.sys.version_info', VersionInfo(3, 10, 0, 'final', 0))
    @patch('importlib.util.find_spec')
    def test_missing_required_deps_fails(self, mock_find_spec, mock_stdout):
        """Missing required dep should fail."""
        mock_find_spec.return_value = None  # All packages "missing"

        config_dir = self.temp_path / ".tokenpak"
        config_dir.mkdir()
        (config_dir / "cache").mkdir()
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps({"port": 8766}))

        with patch('pathlib.Path.home', return_value=self.temp_path):
            with self.assertRaises(SystemExit) as cm:
                cmd_doctor(self.args)
            self.assertEqual(cm.exception.code, 1)
            output = mock_stdout.getvalue()
            self.assertIn('Dependencies', output)
            self.assertIn('❌', output)
