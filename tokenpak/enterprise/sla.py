"""DEPRECATED — `tokenpak.enterprise.sla` moved to
`tokenpak_paid.enterprise.sla` (TPS-11, 2026-04-21).

Install the tokenpak-paid Enterprise tier to keep access:

    tokenpak activate YOUR-KEY
    tokenpak install-tier enterprise

This OSS stub raises ImportError if any attribute is accessed, so
downstream callers see a clear error instead of silently-working
but non-functional stubs.
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.enterprise.sla: moved to tokenpak_paid.enterprise.sla "
    "(Enterprise tier). Install with `tokenpak install-tier enterprise`. "
    "This OSS stub will be removed in tokenpak 2.0.",
    DeprecationWarning,
    stacklevel=2,
)


def __getattr__(name):
    raise ImportError(
        f"tokenpak.enterprise.{sla}.{{name}} is not available in OSS. "
        "Install tokenpak-paid and activate an Enterprise license: "
        "`tokenpak install-tier enterprise`."
    )


__all__: list = []
