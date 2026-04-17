"""
proxy.py — re-export shim for proxy_monolith.py.bak

Loads the monolith module into this namespace so all existing import paths
(spec_from_file_location, sys.path + import, etc.) continue to work.

CCG-17 canary: TTL ordering hotfix v2
"""
from pathlib import Path as _Path

_MONOLITH = _Path(__file__).parent / "proxy_monolith.py.bak"
exec(compile(_MONOLITH.read_text(encoding="utf-8"), str(_MONOLITH), "exec"), globals())  # noqa: S102
