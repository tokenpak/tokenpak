"""CLI commands for debug mode: on / off / status / receipt."""

from __future__ import annotations

__all__ = ("debug_cmd",)


from argparse import Namespace
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from tokenpak.core.debug import DebugState
    from tokenpak.proxy.spend_guard.receipt import ReceiptDebugPointer


def _state() -> DebugState:
    from tokenpak.core.debug import DebugState

    return DebugState()


def debug_cmd(args: Namespace) -> None:
    """Dispatch debug sub-commands."""
    debug_args = getattr(args, "debug_args", None) or []
    sub = getattr(args, "debug_cmd", None) or (debug_args[0] if debug_args else None)
    if sub == "on":
        _cmd_on(args)
    elif sub == "off":
        _cmd_off()
    elif sub == "status":
        _cmd_status()
    elif sub == "receipt":
        _cmd_receipt(args)
    else:
        print("Usage: tokenpak debug <on [--requests N] | off | status | receipt <request_id>>")


def _cmd_receipt(args: Namespace) -> None:
    """Print a redaction-safe Receipt v1 proof object for a recorded request."""
    debug_args = getattr(args, "debug_args", None) or []
    request_id = getattr(args, "request_id", None) or (
        debug_args[1] if len(debug_args) > 1 else None
    )
    redact = not getattr(args, "raw", False)
    print(_render_request_receipt(request_id, redact=redact))


def _resolve_debug_pointer(request_id: str) -> ReceiptDebugPointer:
    """Build a redaction-safe debug-capture pointer for ``request_id``.

    Reports ``present=True`` only when a capture blob actually exists for the
    request; otherwise reports the configured capture mode so the caller knows
    whether debug capture is even enabled (actionable for C18).
    """
    from tokenpak.debug.capture import get_capture_mode, list_captures
    from tokenpak.proxy.spend_guard.receipt import ReceiptDebugPointer

    mode = get_capture_mode().value
    for entry in list_captures():
        if str(entry.get("trace_id", "")) == str(request_id):
            return ReceiptDebugPointer(
                present=True,
                trace_id=str(request_id),
                capture_mode=entry.get("mode", mode),
                path=entry.get("path"),
            )
    return ReceiptDebugPointer(present=False, trace_id=str(request_id), capture_mode=mode)


def _render_request_receipt(request_id: Optional[str], *, redact: bool = True) -> str:
    """Render a Receipt v1 for ``request_id`` as redaction-safe JSON.

    Falls back to a support-bundle pointer message when the id is missing or no
    matching request was recorded (AC-4: "redaction-safe receipt or support
    bundle pointer").
    """
    from tokenpak.cli.request_explorer import get_request_by_id
    from tokenpak.proxy.spend_guard.receipt import (
        build_request_receipt,
        render_receipt,
    )

    if not request_id:
        return _support_bundle_pointer("no request id given")

    row = get_request_by_id(str(request_id))
    if row is None:
        return _support_bundle_pointer(f"no recorded request '{request_id}'")

    receipt = build_request_receipt(row, debug_pointer=_resolve_debug_pointer(str(request_id)))
    return render_receipt(receipt, redact=redact)


def _support_bundle_pointer(reason: str) -> str:
    """Return a redaction-safe pointer to where debug evidence lives."""
    from pathlib import Path

    debug_dir = Path.home() / ".tokenpak" / "debug"
    return (
        f"No receipt: {reason}.\n"
        f"Debug capture bundle: {debug_dir}\n"
        "Enable capture with TOKENPAK_DEBUG_CAPTURE=encrypted (or hash_only), "
        "then list traces with: tokenpak debug list"
    )


def _cmd_on(args: Namespace) -> None:
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
    log_size = st["log_size_bytes"]
    log_kb = round(log_size / 1024, 1) if isinstance(log_size, int) else 0.0
    print(f"Debug mode:    {flag}")
    if enabled:
        print(f"Remaining:     {rem_str}")
    print(f"Log path:      {st['log_path']}")
    print(f"Log size:      {log_kb} KB")
