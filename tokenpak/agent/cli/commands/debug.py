"""CLI commands for debug mode: on / off / status."""

from __future__ import annotations


def _state():
    from tokenpak.agent.debug.state import DebugState

    return DebugState()


def debug_cmd(args) -> None:
    """Dispatch debug sub-commands."""
    sub = getattr(args, "debug_cmd", None) or (
        args.debug_args[0] if getattr(args, "debug_args", None) else None
    )
    if sub == "on":
        _cmd_on(args)
    elif sub == "off":
        _cmd_off()
    elif sub == "status":
        _cmd_status()
    else:
        print("Usage: tokenpak debug <on [--requests N] | off | status>")


def _cmd_on(args) -> None:
    requests = getattr(args, "debug_requests", None)
    _state().enable(requests=requests)
    if requests:
        print(f"Debug mode ON — auto-disables after {requests} request(s).")
    else:
        print("Debug mode ON — unlimited. Logs: ~/.tokenpak/debug.log")


def _cmd_off() -> None:
    _state().disable()
    print("Debug mode OFF.")


def _cmd_status() -> None:
    st = _state().status()
    enabled = st["enabled"]
    flag = "ON" if enabled else "OFF"
    remaining = st["requests_remaining"]
    rem_str = str(remaining) if remaining is not None else "unlimited"
    log_kb = round(st["log_size_bytes"] / 1024, 1)
    print(f"Debug mode:    {flag}")
    if enabled:
        print(f"Remaining:     {rem_str}")
    print(f"Log path:      {st['log_path']}")
    print(f"Log size:      {log_kb} KB")
