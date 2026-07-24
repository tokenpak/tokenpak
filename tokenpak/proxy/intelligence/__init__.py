"""TokenPak Intelligence Server components."""

try:
    import fastapi  # noqa: F401
except ImportError as e:
    raise ImportError(
        "tokenpak.proxy.intelligence requires the [server] extra. "
        "Install with: pip install tokenpak[server]"
    ) from e

__all__ = ["auth", "deep_health", "server"]
