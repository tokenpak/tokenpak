"""Shim: re-export canonical payload types from tokenpak.proxy.adapters.canonical."""

from tokenpak.proxy.adapters.canonical import CanonicalRequest, CanonicalResponse

__all__ = ["CanonicalRequest", "CanonicalResponse"]
