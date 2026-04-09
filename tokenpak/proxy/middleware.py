"""
tokenpak.proxy.middleware — Auth, tunnel, and response helpers for ForwardProxyHandler.

Extracted from proxy/server.py as part of TPK-RESTRUCTURE-012.
Provides a mixin class (ProxyMiddlewareMixin) with:
  - _check_auth()       — IP + key-based authorization
  - do_CONNECT()        — HTTPS CONNECT tunnel handler
  - _tunnel_connect()   — bidirectional TCP tunnel loop
  - _send_json()        — compact JSON response helper
"""

# ---------------------------------------------------------------------------
# stdlib
# ---------------------------------------------------------------------------
import json
import socket
import time


class ProxyMiddlewareMixin:
    """
    Mixin for ForwardProxyHandler providing auth, tunneling, and response helpers.

    All methods use ``self`` as the BaseHTTPRequestHandler instance.
    Mix in before BaseHTTPRequestHandler in the MRO:

        class ForwardProxyHandler(ProxyMiddlewareMixin, BaseHTTPRequestHandler): ...
    """

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _check_auth(self):
        """Check if request is authorized.

        Localhost (127.0.0.1 / ::1) is always trusted.
        Remote clients require X-TokenPak-Key header when PROXY_AUTH_KEY is set.
        """
        from tokenpak.proxy.config import PROXY_AUTH_KEY  # late import to avoid circular dep

        client_ip = self.client_address[0]
        # Localhost always trusted
        if client_ip in ("127.0.0.1", "::1"):
            return True
        # No auth configured → allow (network access at user's risk)
        if not PROXY_AUTH_KEY:
            return True
        # Remote client with auth key configured — check header
        import hmac
        client_key = self.headers.get("X-TokenPak-Key", "")
        return hmac.compare_digest(client_key, PROXY_AUTH_KEY)

    # ------------------------------------------------------------------
    # HTTPS CONNECT tunnel
    # ------------------------------------------------------------------

    def do_CONNECT(self):
        """Handle HTTP CONNECT requests (HTTPS proxying)."""
        host, _, port = self.path.partition(":")
        port = int(port) if port else 443
        self._tunnel_connect(host, port)

    def _tunnel_connect(self, host, port):
        """Open a bidirectional TCP tunnel between client and upstream host:port."""
        try:
            remote = socket.create_connection((host, port), timeout=30)
        except Exception as e:
            self.send_error(502, f"Cannot connect to {host}:{port}: {e}")
            return
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.connection.setblocking(False)
        remote.setblocking(False)
        timeout = 120
        last_activity = time.time()
        while time.time() - last_activity < timeout:
            data_moved = False
            try:
                data = self.connection.recv(65536)
                if data:
                    remote.sendall(data)
                    last_activity = time.time()
                    data_moved = True
                elif data == b"":
                    break
            except BlockingIOError:
                pass
            except Exception:
                break
            try:
                data = remote.recv(65536)
                if data:
                    self.connection.sendall(data)
                    last_activity = time.time()
                    data_moved = True
                elif data == b"":
                    break
            except BlockingIOError:
                pass
            except Exception:
                break
            if not data_moved:
                time.sleep(0.01)
        remote.close()

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_json(self, data, status=200):
        """Send a compact JSON response with CORS and keep-alive headers."""
        body = json.dumps(data, separators=(",", ":")).encode()  # compact: faster + smaller
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(body)
