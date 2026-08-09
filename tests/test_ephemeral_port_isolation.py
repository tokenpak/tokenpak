"""Regression guards for collision-free test listener ports.

Test listeners must use an operating-system-assigned port or the shared
``free_port`` helper. Fixed ports make independent pytest processes interfere
with one another when validation lanes run concurrently.
"""

from __future__ import annotations

import ast
import json
import threading
import urllib.request
from pathlib import Path

from tests.proxy._proxy_subprocess import free_port
from tokenpak.proxy.server import ProxyServer

_ADDRESS_SERVER_CALLS = {
    "HTTPServer",
    "ThreadedHTTPServer",
    "ThreadingHTTPServer",
    "TCPServer",
    "ThreadingTCPServer",
    "_HTTPServer",
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _module_int_constants(tree: ast.Module) -> dict[str, int]:
    constants: dict[str, int] = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue
        resolved = _resolve_int(value, constants)
        if resolved is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                constants[target.id] = resolved
    return constants


def _resolve_int(node: ast.AST | None, constants: dict[str, int]) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _resolve_int(node.operand, constants)
        return -value if value is not None else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
        left = _resolve_int(node.left, constants)
        right = _resolve_int(node.right, constants)
        if left is None or right is None:
            return None
        return left + right if isinstance(node.op, ast.Add) else left - right
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"int", "str"}
        and len(node.args) == 1
    ):
        return _resolve_int(node.args[0], constants)
    return None


def _address_port(node: ast.AST | None) -> ast.AST | None:
    if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) >= 2:
        return node.elts[1]
    return None


def _bound_port_expression(call: ast.Call) -> ast.AST | None:
    name = _call_name(call)
    if name == "bind" and call.args:
        return _address_port(call.args[0])
    if name == "ProxyServer":
        for keyword in call.keywords:
            if keyword.arg == "port":
                return keyword.value
        return call.args[1] if len(call.args) >= 2 else None
    if name in _ADDRESS_SERVER_CALLS and call.args:
        return _address_port(call.args[0])
    return None


def _fixed_subprocess_port(call: ast.Call, constants: dict[str, int]) -> int | None:
    if _call_name(call) not in {"Popen", "run", "check_call", "check_output"}:
        return None
    if not call.args or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        return None
    argv = call.args[0].elts
    for index, argument in enumerate(argv[:-1]):
        if isinstance(argument, ast.Constant) and argument.value == "--port":
            return _resolve_int(argv[index + 1], constants)
    return None


def test_proxy_spawning_tests_do_not_use_fixed_ports() -> None:
    """Regression: fixed test listeners caused concurrent-suite Errno 98 failures."""
    test_root = Path(__file__).resolve().parent
    violations: list[str] = []

    for path in sorted(test_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constants = _module_int_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            port = _resolve_int(_bound_port_expression(node), constants)
            if port is None:
                port = _fixed_subprocess_port(node, constants)
            if port not in (None, 0):
                violations.append(f"{path.relative_to(test_root)}:{node.lineno}: port {port}")

    assert not violations, (
        "fixed test listener ports can collide across concurrent suites; use port 0 "
        "for stdlib servers or tests.proxy._proxy_subprocess.free_port():\n" + "\n".join(violations)
    )


def test_two_proxy_fixtures_can_run_concurrently() -> None:
    """Two independent proxy fixtures bind and answer health checks together."""
    first_port = free_port()
    second_port = free_port()
    while second_port == first_port:
        second_port = free_port()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def exercise(port: int) -> None:
        server = ProxyServer(host="127.0.0.1", port=port)
        try:
            barrier.wait(timeout=5)
            server.start(blocking=False)
            request = urllib.request.Request(f"http://127.0.0.1:{server.port}/health")
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
                assert json.loads(response.read())["status"] in {"ok", "degraded"}
        except BaseException as exc:
            with errors_lock:
                errors.append(exc)
        finally:
            server.stop()

    threads = [
        threading.Thread(target=exercise, args=(port,)) for port in (first_port, second_port)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(not thread.is_alive() for thread in threads), "concurrent proxy fixture hung"
    assert not errors, f"concurrent proxy fixtures failed: {errors!r}"
