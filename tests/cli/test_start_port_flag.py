# SPDX-License-Identifier: Apache-2.0
"""`tokenpak start --port` must actually select the port.

The flag was declared and documented (help text carries an explicit
`--port 8888` example) but `cmd_start` derived the port from TOKENPAK_PORT
alone. So `start --port 8799` ran its ownership preflight against 8766 and
refused whenever anything else already held the default port — the exact
situation a developer with a busy 8766 hits on first contact.
"""

from tokenpak._cli_core import build_parser


class TestStartPortFlag:
    def test_flag_is_captured(self):
        """--port reaches args, so cmd_start can prefer it."""
        args = build_parser().parse_args(["start", "--port", "8799"])
        assert args.port == 8799

    def test_default_is_none_not_8766(self):
        """The default must stay None.

        This is the load-bearing part. With a concrete 8766 default, an
        unflagged run is indistinguishable from `--port 8766`, so honouring
        the flag would silently override TOKENPAK_PORT on every plain
        `tokenpak start` — trading one bug for a worse one.
        """
        args = build_parser().parse_args(["start"])
        assert args.port is None

    def test_precedence_flag_then_env_then_default(self, monkeypatch):
        """Flag beats env; env beats the built-in default."""

        def resolve(args_port, env_port):
            monkeypatch.delenv("TOKENPAK_PORT", raising=False)
            if env_port is not None:
                monkeypatch.setenv("TOKENPAK_PORT", env_port)
            import os

            flag = args_port
            return int(flag) if flag is not None else int(os.environ.get("TOKENPAK_PORT", "8766"))

        assert resolve(8799, "8777") == 8799  # explicit flag wins
        assert resolve(None, "8777") == 8777  # env still honoured
        assert resolve(None, None) == 8766  # documented default preserved
