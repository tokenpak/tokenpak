"""`python -m tokenpak.proxy` — launch the proxy server in the foreground.

Used by systemd units (e.g. `tokenpak-proxy.service`) and by callers who
want a blocking, env-configured launch without going through the
`tokenpak start` CLI which forks the daemon.

Configuration is env-driven:

  TOKENPAK_PORT            default 8766
  TOKENPAK_BIND            default 127.0.0.1

Any crash inside ``start_proxy`` propagates naturally so systemd can log
it and decide whether to restart.
"""

from __future__ import annotations

import os

from tokenpak.proxy.server import start_proxy


def main() -> None:
    bind = os.environ.get("TOKENPAK_BIND", "127.0.0.1")
    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    start_proxy(host=bind, port=port, blocking=True)


if __name__ == "__main__":
    main()
